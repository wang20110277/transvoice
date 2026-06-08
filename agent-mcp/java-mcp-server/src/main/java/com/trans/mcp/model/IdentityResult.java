package com.trans.mcp.model;

import com.fasterxml.jackson.annotation.JsonProperty;

public record IdentityResult(
		@JsonProperty("user_id") String userId,
		@JsonProperty("phone_masked") String phoneMasked,
		@JsonProperty("id_card_last_four") String idCardLastFour
) {
}
