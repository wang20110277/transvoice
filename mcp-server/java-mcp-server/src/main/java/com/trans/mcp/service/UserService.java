package com.trans.mcp.service;

import com.trans.mcp.model.IdentityResult;
import org.springframework.ai.mcp.annotation.McpTool;
import org.springframework.ai.mcp.annotation.McpToolParam;
import org.springframework.stereotype.Component;

@Component
public class UserService {

	@McpTool(name = "user_identity_query", description = "根据手机号查询用户中心，获取用户ID、脱敏手机号、身份证后四位")
	public IdentityResult user_identity_query(
			@McpToolParam(description = "用户手机号", required = true) String phone,
			@McpToolParam(description = "业务类型：customer_service / collection / marketing", required = true) String biz_type) {
		if (phone == null || !phone.matches("1\\d{10}")) {
			throw new IllegalArgumentException("手机号格式不正确，应为11位数字且以1开头");
		}
		// TODO: 接入真实用户中心数据源
		return new IdentityResult(
				"USER_" + Math.abs(phone.hashCode() % 100000),
				phone.substring(0, 3) + "****" + phone.substring(7),
				"1234"
		);
	}
}
