/**
 * prompt-template 单测 — 对齐 agent-flow tests/graph/test_render.py 语义。
 * 纯逻辑,无外部依赖。
 */
import { describe, it, expect } from 'vitest';
import { extractVariables, renderPrompt } from '../../src/lib/prompt-template';

describe('extractVariables', () => {
  it('扫描单个占位符', () => {
    expect(extractVariables('hi {name}')).toEqual(['name']);
  });

  it('扫描多个占位符并去重保序', () => {
    expect(extractVariables('{a} and {b} and {a}')).toEqual(['a', 'b']);
  });

  it('仅匹配合法变量名(字母/下划线/数字),忽略 JSON 花括号', () => {
    expect(extractVariables('{"key": 1} 和 {customer_name}')).toEqual(['customer_name']);
  });

  it('无占位符返回空数组', () => {
    expect(extractVariables('no placeholders')).toEqual([]);
  });

  it('JSON 花括号不误判为变量(内容非合法变量名)', () => {
    // {"key":1} 的 { 后接 " 不匹配合法变量名正则,故跳过
    expect(extractVariables('config={"k":1} use {customer_name}')).toEqual(['customer_name']);
  });
});

describe('renderPrompt', () => {
  it('替换存在的变量', () => {
    expect(renderPrompt('hi {name}', { name: 'X' })).toBe('hi X');
  });

  it('多变量替换', () => {
    expect(renderPrompt('{a} and {b}', { a: '1', b: '2' })).toBe('1 and 2');
  });

  it('缺失变量保留占位符原样(不崩)', () => {
    expect(renderPrompt('hi {name}', {})).toBe('hi {name}');
  });

  it('无占位符原样返回', () => {
    expect(renderPrompt('plain text', {})).toBe('plain text');
  });

  it('非字符串值强转为字符串', () => {
    expect(renderPrompt('amount={amount}', { amount: 1200 })).toBe('amount=1200');
  });

  it('模板有占位符但 vars 无该键 → 保留占位符', () => {
    expect(renderPrompt('hi {stranger}', { name: 'X' })).toBe('hi {stranger}');
  });

  it('空模板返回空', () => {
    expect(renderPrompt('', { name: 'X' })).toBe('');
  });

  it('重复占位符全部替换', () => {
    expect(renderPrompt('{x}-{x}', { x: 'Y' })).toBe('Y-Y');
  });
});
