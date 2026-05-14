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
    messages = [SystemMessage(content=system_content)]

    if chat_history:
        messages.extend(chat_history)

    messages.append(HumanMessage(content=user_input))
    return messages
