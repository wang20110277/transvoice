/**
 * csv-import 单测 — 固定 5 列结构化导入解析 + 占位符比对（纯函数）。
 * vars 列为 key:value|key:value 字符串格式（不使用 JSON）。
 */
import { describe, it, expect } from 'vitest';
import {
  parseImportCsv,
  extractPlaceholders,
  IMPORT_TEMPLATE_CSV,
} from '../../src/lib/csv-import';

describe('extractPlaceholders', () => {
  it('扫描 {占位符}', () => {
    expect(extractPlaceholders('你好 {customer_name}，欠款 {amount}')).toEqual([
      'customer_name', 'amount',
    ]);
  });
  it('空/undefined 安全', () => {
    expect(extractPlaceholders(undefined)).toEqual([]);
    expect(extractPlaceholders('no vars')).toEqual([]);
  });
});

describe('parseImportCsv — 表头与列别名', () => {
  it('中文表头 5 列', () => {
    const csv = '序号,业务类型,手机号,客户id,vars\n1,collection,13800138000,C1,name:张三';
    const r = parseImportCsv(csv, ['name']);
    expect(r.hasHeader).toBe(true);
    expect(r.hasPhoneColumn).toBe(true);
    expect(r.validCount).toBe(1);
    expect(r.rows[0].phone).toBe('13800138000');
    expect(r.rows[0].customerId).toBe('C1');
    expect(r.rows[0].vars).toEqual({ name: '张三' });
  });
  it('英文别名表头', () => {
    const csv = 'seq,biz_type,phone,customer_id,vars\n1,collection,1000,C1,a:1';
    const r = parseImportCsv(csv, ['a']);
    expect(r.rows[0].phone).toBe('1000');
    expect(r.rows[0].vars).toEqual({ a: '1' });
  });
  it('无表头按固定 5 列顺序', () => {
    const csv = '1,collection,1000,C1,a:1';
    const r = parseImportCsv(csv);
    expect(r.hasHeader).toBe(false);
    expect(r.rows[0].phone).toBe('1000');
  });
});

describe('parseImportCsv — vars 列引号与逗号', () => {
  it('value 含逗号须整体引号包裹', () => {
    // 单元格："name:张三|amount:1,200" → 逗号在引号内，整体为一个字段
    const csv = '序号,业务类型,手机号,客户id,vars\n1,c,1000,C1,"name:张三|amount:1,200"';
    const r = parseImportCsv(csv);
    expect(r.rows[0].vars).toEqual({ name: '张三', amount: '1,200' });
    expect(r.errorCount).toBe(0);
  });
  it('value 不含逗号无需引号', () => {
    const csv = '序号,业务类型,手机号,客户id,vars\n1,c,1000,C1,name:张三';
    const r = parseImportCsv(csv);
    expect(r.rows[0].vars).toEqual({ name: '张三' });
  });
  it('坏对（无冒号 / 空 key）静默跳过，不报错', () => {
    // badpair 无 ':' → 跳过；:only 空 key → 跳过；name:张三 保留
    const csv = '序号,业务类型,手机号,客户id,vars\n1,c,1000,C1,name:张三|badpair|:only';
    const r = parseImportCsv(csv);
    expect(r.rows[0].vars).toEqual({ name: '张三' });
    expect(r.rows[0].error).toBeUndefined();
    expect(r.errorCount).toBe(0);
  });
  it('value 含冒号（按首个冒号拆，如时间 09:30）', () => {
    const csv = '序号,业务类型,手机号,客户id,vars\n1,c,1000,C1,call_time:09:30';
    const r = parseImportCsv(csv);
    expect(r.rows[0].vars).toEqual({ call_time: '09:30' });
  });
  it('空串 → {}', () => {
    const csv = '序号,业务类型,手机号,客户id,vars\n1,c,1000,C1,';
    const r = parseImportCsv(csv);
    expect(r.rows[0].vars).toEqual({});
    expect(r.rows[0].error).toBeUndefined();
  });
});

describe('parseImportCsv — 错误与警告', () => {
  it('手机号空行 → 行级 error', () => {
    const csv = '序号,业务类型,手机号,客户id,vars\n1,c,,C1,a:1';
    const r = parseImportCsv(csv);
    expect(r.rows[0].error).toMatch(/手机号/);
    expect(r.validCount).toBe(0);
  });
  it('业务类型 ≠ 任务 biz_type → 警告不阻断', () => {
    const csv = '序号,业务类型,手机号,客户id,vars\n1,marketing,1000,C1,a:1';
    const r = parseImportCsv(csv, [], 'collection');
    expect(r.rows[0].warning).toMatch(/marketing/);
    expect(r.rows[0].error).toBeUndefined();
    expect(r.warningCount).toBe(1);
    expect(r.validCount).toBe(1);
  });
  it('部分错误行 → 允许有效行', () => {
    const csv = '序号,业务类型,手机号,客户id,vars\n1,c,1000,C1,a:1\n2,c,,C1,badpair';
    const r = parseImportCsv(csv);
    expect(r.totalRows).toBe(2);
    expect(r.validCount).toBe(1);
    expect(r.errorCount).toBe(1);
  });
});

describe('parseImportCsv — 占位符比对', () => {
  it('hit/missing/extra + 覆盖度', () => {
    const csv = '序号,业务类型,手机号,客户id,vars\n'
      + '1,c,1000,C1,name:张三|amount:100\n'
      + '2,c,1001,C2,name:李四\n';
    const r = parseImportCsv(csv, ['name', 'amount', 'phone']);
    expect(r.placeholders.hit.sort()).toEqual(['amount', 'name']);
    expect(r.placeholders.missing).toEqual(['phone']); // 无任何行提供
    expect(r.placeholders.extra).toEqual([]); // name/amount 都是占位符
    expect(r.placeholders.perVarCoverage.name).toBe(2);
    expect(r.placeholders.perVarCoverage.amount).toBe(1);
    expect(r.placeholders.perVarCoverage.phone).toBe(0);
  });
  it('多余 key → extra', () => {
    const csv = '序号,业务类型,手机号,客户id,vars\n1,c,1000,C1,name:张三|extra_key:x';
    const r = parseImportCsv(csv, ['name']);
    expect(r.placeholders.extra).toEqual(['extra_key']);
  });
});

describe('IMPORT_TEMPLATE_CSV', () => {
  it('模板含 5 列表头 + 可被解析', () => {
    const r = parseImportCsv(IMPORT_TEMPLATE_CSV, ['customer_name', 'amount']);
    expect(r.hasHeader).toBe(true);
    expect(r.validCount).toBe(1);
    expect(r.rows[0].customerId).toBe('C10001');
  });
});
