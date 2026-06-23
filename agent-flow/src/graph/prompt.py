"""Prompt 组装 - LangChain 消息列表格式"""
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage


def build_messages(
    biz_type: str,
    system_prompt: str,
    user_input: str,
    memory_block: str = "",
    rag_block: str = "",
    chat_history: list[BaseMessage] | None = None,
) -> list:
    """组装 LangChain 消息列表：system + RAG + memory + history + user"""
    parts = [system_prompt]

    if rag_block:
        parts.append(rag_block)

    if memory_block:
        parts.append(memory_block)

    system_content = "\n\n".join(parts)

    # 非首轮(已有历史):注入运行时约束,压制系统提示词 Initialization 里
    # "首先问候…例如'您好,欢迎致电…'" 导致的每轮重复问候;首轮不动。
    if chat_history:
        system_content += (
            "\n\n【运行时约束】通话已经开始且你已完成开场问候。"
            "禁止再次问候、自我介绍或说\"欢迎致电\"。直接回答用户最新一句话。"
        )

    messages = [SystemMessage(content=system_content)]

    if chat_history:
        messages.extend(chat_history)

    messages.append(HumanMessage(content=f"{user_input}\n/no_think"))
    return messages
