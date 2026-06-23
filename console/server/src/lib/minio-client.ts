/**
 * Console 侧 MinIO client — 仅生成 presigned GET URL（通话录音播放）。
 * 与 agent-flow 共享同一 MinIO 实例 + 同一 bucket（MINIO_* env）。
 * MINIO_ENDPOINT 为空时返回 null（console 显示"录音未归档"）。
 */
import { Client } from 'minio';

const ENDPOINT = process.env.MINIO_ENDPOINT ?? '';
const ACCESS_KEY = process.env.MINIO_ACCESS_KEY ?? '';
const SECRET_KEY = process.env.MINIO_SECRET_KEY ?? '';
const BUCKET = process.env.MINIO_BUCKET ?? 'audio-archive';
const SECURE = (process.env.MINIO_SECURE ?? 'false').toLowerCase() === 'true';

function client(): Client | null {
  if (!ENDPOINT) return null;
  // MINIO_ENDPOINT 形如 "127.0.0.1:9000"
  const [end, portStr] = ENDPOINT.split(':');
  const port = portStr ? Number(portStr) : 9000;
  return new Client({
    endPoint: end, port, useSSL: SECURE,
    accessKey: ACCESS_KEY, secretKey: SECRET_KEY,
  });
}

/** 生成录音 presigned GET URL（默认 1h）。MinIO 未配置或异常返回 null。 */
export async function presignedRecordingUrl(objectKey: string, expirySec = 3600): Promise<string | null> {
  const c = client();
  if (!c) return null;
  try {
    return await c.presignedGetObject(BUCKET, objectKey, expirySec);
  } catch (e) {
    // eslint-disable-next-line no-console
    console.error('presignedRecordingUrl failed:', e);
    return null;
  }
}
