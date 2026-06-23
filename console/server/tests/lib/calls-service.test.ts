import { describe, it, expect } from 'vitest';
import { toSessionDTO } from '../../src/lib/calls-service';

describe('calls-service toSessionDTO', () => {
  const baseRow = {
    id: 1, userId: 'u1', callId: 'c1', fsUuid: 'c1', tenantId: 'default',
    bizType: 'marketing', scenario: 'default', taskId: null,
    phoneHash: 'SECRET_HASH', userKey: '13812345678', phoneMasked: '138****5678',
    startTs: new Date('2026-06-23T10:00:00Z'),
    endTs: null, resultCode: null, hangupCause: null,
    identityVerified: false, verifyAttempts: 0, recordingNoticePlayed: true,
    createTime: new Date(), createUser: 'system', updateTime: new Date(), updateUser: 'system',
  };

  it('never leaks phone_hash (only phone_masked to client)', () => {
    const dto = toSessionDTO(baseRow);
    expect(dto.phoneMasked).toBe('138****5678');
    expect((dto as unknown as Record<string, unknown>).phoneHash).toBeUndefined();
  });

  it('durationMs null when endTs null', () => {
    expect(toSessionDTO(baseRow).durationMs).toBeNull();
  });

  it('durationMs = endTs - startTs in ms', () => {
    const dto = toSessionDTO({ ...baseRow, endTs: new Date('2026-06-23T10:00:10Z') });
    expect(dto.durationMs).toBe(10000);
  });

  it('exposes callId + bizType + tenantId for list/detail display', () => {
    const dto = toSessionDTO(baseRow);
    expect(dto.callId).toBe('c1');
    expect(dto.bizType).toBe('marketing');
    expect(dto.tenantId).toBe('default');
  });
});
