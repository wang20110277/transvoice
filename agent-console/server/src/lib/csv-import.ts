/**
 * 结构化号码清单导入解析（纯函数，无副作用，可单测）。
 *
 * 固定 5 列模板：序号 | 业务类型 | 手机号 | 客户id | vars
 *   - 序号/业务类型：仅校验/展示，不入业务字段（biz_type=任务 biz_type，seq 不入库）
 *   - 手机号：去空白后非空 = 唯一硬错误判据（不格式强校验，兼容测试分机号 1000）
 *   - 客户id：可选，原样留存
 *   - vars：key:value|key:value 字符串解析成 Record<string,string>，空串/缺省 → {}
 *           按 '|' 拆对、每对按首个 ':' 拆 key/value（允许值含 ':'），trim，跳过空对/无 ':' 对
 *           （不使用 JSON；坏对静默跳过，靠占位符覆盖面板反馈缺失）
 *
 * 占位符比对：单一真相源是任务绑定 prompt 的 {占位符} 集合（placeholders 入参）。
 *   - hit  = 占位符 ∩ vars keys 并集
 *   - missing = 占位符 − vars keys 并集（没有任何行提供的占位符）
 *   - extra  = vars keys 并集 − 占位符（vars 里多余、prompt 用不上的 key）
 *   - perVarCoverage = 每个占位符在多少行 vars 中命中
 *
 * CSV 引号处理：vars 列一般不含逗号（key:value|key:value），但值若含逗号仍须整体引号包裹；
 * splitCsvLine 容忍 "a,b" 内逗号 + 两端引号剥离（最小实现）。
 */

/** 列名别名（兼容中英文表头），统一映射到规范列。 */
const SEQ_ALIASES = new Set(['序号', 'seq', 'sequence', 'no', 'index']);
const BIZ_ALIASES = new Set(['业务类型', 'biz_type', 'biztype', 'type']);
const PHONE_ALIASES = new Set(['手机号', 'phone', 'mobile', '号码', 'tel']);
const CUSTOMER_ALIASES = new Set(['客户id', '客户id号', 'customer_id', 'customerid', 'cust_id']);
const VARS_ALIASES = new Set(['json', 'vars', 'variables', '变量']);

export interface ParsedRow {
  seq?: string;
  bizType?: string;
  phone: string;
  customerId?: string;
  vars: Record<string, string>;
  error?: string;    // 行级硬错误（malformed json / 缺手机号）→ 该行不入库
  warning?: string;  // 行级软警告（biz_type 不匹配）→ 不阻断入库
}

export interface PlaceholderCoverage {
  hit: string[];                       // 占位符 ∩ json keys 并集
  missing: string[];                   // 占位符 − json keys 并集（全表无人提供）
  extra: string[];                     // json keys 并集 − 占位符（多余 key）
  perVarCoverage: Record<string, number>; // 每个占位符命中的行数
}

export interface ParseResult {
  rows: ParsedRow[];
  totalRows: number;
  validCount: number;
  errorCount: number;
  warningCount: number;
  hasHeader: boolean;
  hasPhoneColumn: boolean;
  placeholders: PlaceholderCoverage;
}

/** 提取 prompt 内容里的 {占位符} 名（字母/数字/下划线），供预览比对。 */
export function extractPlaceholders(promptText: string | undefined | null): string[] {
  if (!promptText) return [];
  const set = new Set<string>();
  const re = /\{([A-Za-z_][\w]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(promptText)) !== null) set.add(m[1]);
  return [...set];
}

/**
 * CSV 行切分（RFC 4180 子集）：仅当字段以 `"` 起始时才视为引号字段，
 * 字段内的 `"` 原样保留；引号字段内 `""` 转义为单个 `"`。
 *
 * vars 列默认 key:value|key:value（不含逗号、不以 `"` 起始 → 按字面字段处理）；
 * 仅当某 value 含逗号时才须整体引号包裹并转义内部引号：`"a:1,200|b:2"`。
 */
function splitCsvLine(line: string): string[] {
  const fields: string[] = [];
  let cur = '';
  let inQuotes = false;
  let fieldStarted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (!fieldStarted && ch === '"') {
      inQuotes = true;
      fieldStarted = true;
      continue;
    }
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } // 转义引号
        else inQuotes = false; // 关闭引号
        continue;
      }
      cur += ch;
      continue;
    }
    if (ch === ',') {
      fields.push(cur);
      cur = '';
      fieldStarted = false;
      continue;
    }
    cur += ch;
    fieldStarted = true;
  }
  fields.push(cur);
  return fields;
}

function resolveColumnIndex(headers: string[]): {
  seq?: number; biz?: number; phone?: number; customer?: number; vars?: number;
} {
  const norm = (s: string) => s.trim().toLowerCase();
  const map = { seq: SEQ_ALIASES, biz: BIZ_ALIASES, phone: PHONE_ALIASES, customer: CUSTOMER_ALIASES, vars: VARS_ALIASES };
  const out: Record<string, number | undefined> = {};
  headers.forEach((h, i) => {
    const n = norm(h);
    for (const [key, aliases] of Object.entries(map)) {
      if (aliases.has(n) || aliases.has(h.trim())) {
        if (out[key] === undefined) out[key] = i;
      }
    }
  });
  return out as { seq?: number; biz?: number; phone?: number; customer?: number; vars?: number };
}

/** 解析 vars 列（key:value|key:value）→ Record<string,string>。空串/缺省 → {}。
 *  不使用 JSON；坏对（无 ':' / 空 key）静默跳过，第二返回值恒 undefined（无硬错误）。 */
function parseVars(raw: string | undefined): [Record<string, string>, string?] {
  const text = (raw ?? '').trim();
  if (text === '') return [{}, undefined];
  const vars: Record<string, string> = {};
  for (const pair of text.split('|')) {
    const p = pair.trim();
    if (!p || !p.includes(':')) continue;
    const idx = p.indexOf(':');
    const key = p.slice(0, idx).trim();
    if (!key) continue;
    vars[key] = p.slice(idx + 1).trim();
  }
  return [vars, undefined];
}

/**
 * 解析 CSV 文本。
 * @param csvText  粘贴或 FileReader 读出的文本
 * @param placeholders  任务绑定 prompt 的占位符集合（用于 hit/missing/extra 比对）
 * @param taskBizType   任务 biz_type（用于「业务类型」列一致性软警告）
 */
export function parseImportCsv(
  csvText: string,
  placeholders: string[] = [],
  taskBizType?: string,
): ParseResult {
  const lines = csvText.split(/\r?\n/).filter((l) => l.trim().length > 0);
  const placeholderSet = new Set(placeholders);

  if (lines.length === 0) {
    return {
      rows: [], totalRows: 0, validCount: 0, errorCount: 0, warningCount: 0,
      hasHeader: false, hasPhoneColumn: false,
      placeholders: { hit: [], missing: [...placeholders], extra: [], perVarCoverage: {} },
    };
  }

  // 首行若命中任一规范列名 → 视为表头
  const firstFields = splitCsvLine(lines[0]).map((f) => f.trim().toLowerCase());
  const looksLikeHeader = firstFields.some(
    (f) => PHONE_ALIASES.has(f) || SEQ_ALIASES.has(f) || VARS_ALIASES.has(f) || CUSTOMER_ALIASES.has(f) || BIZ_ALIASES.has(f),
  );

  let cols: { seq?: number; biz?: number; phone?: number; customer?: number; vars?: number };
  let dataLines: string[];

  if (looksLikeHeader) {
    const headers = splitCsvLine(lines[0]);
    cols = resolveColumnIndex(headers);
    dataLines = lines.slice(1);
  } else {
    // 无表头：按固定 5 列顺序 [序号, 业务类型, 手机号, 客户id, json]
    cols = { seq: 0, biz: 1, phone: 2, customer: 3, vars: 4 };
    dataLines = lines;
  }

  const hasPhoneColumn = cols.phone !== undefined;
  const rows: ParsedRow[] = [];
  const allJsonKeys = new Set<string>();
  const perVarCoverage: Record<string, number> = {};
  placeholders.forEach((p) => (perVarCoverage[p] = 0));
  let errorCount = 0;
  let warningCount = 0;

  dataLines.forEach((line) => {
    const fields = splitCsvLine(line);
    const at = (i: number | undefined) => (i === undefined ? '' : (fields[i] ?? '').trim());
    const phone = at(cols.phone);
    const [vars, varsError] = parseVars(at(cols.vars));

    let error: string | undefined;
    if (!hasPhoneColumn || phone === '') {
      error = '缺少手机号';
    } else if (varsError) {
      error = varsError;
    }

    const bizType = at(cols.biz) || undefined;
    let warning: string | undefined;
    if (taskBizType && bizType && bizType !== taskBizType) {
      warning = `业务类型「${bizType}」≠ 任务「${taskBizType}」`;
    }

    if (!error) {
      Object.keys(vars).forEach((k) => allJsonKeys.add(k));
      placeholders.forEach((p) => {
        if (k(vars, p)) perVarCoverage[p] = (perVarCoverage[p] ?? 0) + 1;
      });
    }
    if (error) errorCount++;
    if (warning) warningCount++;

    rows.push({
      seq: at(cols.seq) || undefined,
      bizType,
      phone: phone || '',
      customerId: at(cols.customer) || undefined,
      vars,
      error,
      warning,
    });
  });

  const hit = placeholders.filter((p) => allJsonKeys.has(p));
  const missing = placeholders.filter((p) => !allJsonKeys.has(p));
  const extra = [...allJsonKeys].filter((k) => !placeholderSet.has(k));

  return {
    rows,
    totalRows: rows.length,
    validCount: rows.length - errorCount,
    errorCount,
    warningCount,
    hasHeader: looksLikeHeader,
    hasPhoneColumn,
    placeholders: { hit, missing, extra, perVarCoverage },
  };
}

/** 小工具：vars 是否含某 key（供 perVarCoverage 内联用）。 */
function k(vars: Record<string, string>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(vars, key);
}

/** 固定 5 列空表头模板 CSV（+ 一行示例），供「下载模板」按钮导出。
 *  vars 列为 key:value|key:value 字符串（不含逗号，无需引号包裹）。 */
export const IMPORT_TEMPLATE_CSV =
  '序号,业务类型,手机号,客户id,vars\n' +
  '1,collection,138****5678,C10001,customer_name:张三|amount:1200.50\n';
