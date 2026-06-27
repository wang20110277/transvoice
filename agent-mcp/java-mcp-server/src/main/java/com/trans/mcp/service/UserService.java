package com.trans.mcp.service;

import java.util.Map;

import com.trans.mcp.model.IdentityResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.ai.mcp.annotation.McpTool;
import org.springframework.ai.mcp.annotation.McpToolParam;
import org.springframework.stereotype.Component;

@Component
public class UserService {

	private static final Logger log = LoggerFactory.getLogger(UserService.class);

	// 内置桩数据（mock）：phone → {userId, idCard}。未接真实用户库，仅本地测试用。
	// 生产应替换为对用户中心的真实查询。phone=1000 为外呼测试分机号。
	private static final Map<String, String[]> MOCK_USERS = Map.of(
			"1000", new String[]{"C10001", "130121198910172233"}
	);

	@McpTool(name = "user_identity_query", description = "根据手机号查询用户中心，获取用户ID、脱敏手机号、身份证后四位")
	public IdentityResult user_identity_query(
			@McpToolParam(description = "用户手机号", required = true) String phone,
			@McpToolParam(description = "业务类型：customer_service / collection / marketing", required = true) String biz_type) {
		log.info("user_identity_query call: phone={} biz_type={}", phone, biz_type);
		if (phone == null || phone.isBlank()) {
			throw new IllegalArgumentException("手机号不能为空");
		}
		// mock 命中：返回桩身份证，截取后四位
		String[] entry = MOCK_USERS.get(phone);
		if (entry != null) {
			String userId = entry[0];
			String idCard = entry[1];
			IdentityResult result = new IdentityResult(userId, maskPhone(phone), idCard.substring(idCard.length() - 4));
			log.info("user_identity_query return(mock): userId={} idCard={} → {}", userId, idCard, result);
			return result;
		}
		IdentityResult result = new IdentityResult(
				"USER_" + Math.abs(phone.hashCode() % 100000),
				phone.substring(0, 3) + "****" + phone.substring(7),
				"1234"
		);
		log.info("user_identity_query return(fallback): {}", result);
		return result;
	}

	private static String maskPhone(String phone) {
		if (phone.length() >= 7) {
			return phone.substring(0, 3) + "****" + phone.substring(phone.length() - 4);
		}
		return phone;
	}
}
