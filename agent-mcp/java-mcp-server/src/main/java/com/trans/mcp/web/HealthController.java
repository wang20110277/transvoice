package com.trans.mcp.web;

import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 健康检查端点。路径固定为 /healthz，与 scripts/local.sh 的 is_running() 探活路径一致 ——
 * 缺它则 stop_svc mcp 会因探活失败而误判"未运行"并跳过停止。
 */
@RestController
public class HealthController {

	@GetMapping("/healthz")
	public Map<String, Object> healthz() {
		return Map.of("status", "UP");
	}

}
