package com.trans.mcp.service;

import com.trans.mcp.model.CreditResult;
import org.springframework.ai.mcp.annotation.McpTool;
import org.springframework.ai.mcp.annotation.McpToolParam;
import org.springframework.stereotype.Component;

@Component
public class CreditService {

	@McpTool(name = "user_credit_query", description = "根据用户ID查询征信信息，返回是否合格及风险等级，仅 marketing 业务类型使用")
	public CreditResult user_credit_query(
			@McpToolParam(description = "用户唯一标识，由身份查询返回", required = true) String user_id) {
		// TODO: 接入真实征信数据源
		return new CreditResult(
				user_id,
				true,
				"low"
		);
	}
}
