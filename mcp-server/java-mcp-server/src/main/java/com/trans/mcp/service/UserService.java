package com.trans.mcp.service;

import com.trans.mcp.model.IdentityResult;
import org.springframework.ai.tool.annotation.Tool;
import org.springframework.ai.tool.annotation.ToolParam;
import org.springframework.stereotype.Service;

@Service
public class UserService {

	@Tool(description = "根据手机号查询用户中心，获取用户ID、脱敏手机号、身份证后四位")
	public IdentityResult user_identity_query(
			@ToolParam(description = "用户手机号") String phone,
			@ToolParam(description = "业务类型：customer_service / collection / marketing") String biz_type) {
		// TODO: 接入真实用户中心数据源
		return new IdentityResult(
				"USER_" + Math.abs(phone.hashCode() % 100000),
				phone.substring(0, 3) + "****" + phone.substring(phone.length() - 4),
				"1234"
		);
	}
}
