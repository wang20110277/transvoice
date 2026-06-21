/**
 * 提示词模板:变量提取与渲染。
 *
 * 语义与 agent-flow src/graph/render.py 严格对齐 — Console 的联调预览即呼入运行时
 * 实际渲染结果,杜绝"预览与上线不一致"。
 *
 * 占位符形式 {name},name = [A-Za-z_][A-Za-z0-9_]*。
 * 缺失变量 → 保留占位符原样(可观测,不崩)。
 */

// 匹配 {name} 形式占位符;排除 {{ }} 转义与 JSON 花括号片段(仅字母/下划线起头)
const PLACEHOLDER_RE = /\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

/** 扫描模板内所有 {name} 占位符,去重保序。 */
export function extractVariables(template: string): string[] {
  if (!template) return [];
  const seen = new Set<string>();
  const order: string[] = [];
  for (const m of template.matchAll(PLACEHOLDER_RE)) {
    const name = m[1];
    if (!seen.has(name)) {
      seen.add(name);
      order.push(name);
    }
  }
  return order;
}

/**
 * 渲染模板:对 vars 中存在的键替换 {name};缺失则保留占位符原样。
 * 非 string 值强转为字符串(与 Python 端 str() 一致)。
 */
export function renderPrompt(template: string, vars: Record<string, unknown>): string {
  if (!template) return template;
  return template.replace(PLACEHOLDER_RE, (full, name: string) =>
    name in vars ? String(vars[name]) : full,
  );
}
