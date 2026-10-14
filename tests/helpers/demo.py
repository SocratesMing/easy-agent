# from langchain_deepseek import ChatDeepSeek

# llm = ChatDeepSeek(
#     model="deepseek-v4-flash",
#     temperature=0,
#     max_tokens=None,
#     timeout=None,
#     max_retries=2,
#     api_key=os.environ["DEEPSEEK_API_KEY"],
#     base_url="https://api.deepseek.com",
#     # other params...
# )

# messages = [
#     (
#         "system",
#         "你是一个智能体助手",
#     ),
#     ("human", "你好"),
# ]
# ai_msg = llm.invoke(messages)
# print(ai_msg.content)
# reasoning = ai_msg.additional_kwargs.get("reasoning_content")  # 提取思维链

# # 4. 打印结果
# print(f"思考过程: {reasoning}")


import os

from langchain_openai import ChatOpenAI

api_key = os.getenv("DEEPSEEK_API_KEY")
if not api_key:
    raise RuntimeError("Please set DEEPSEEK_API_KEY before running this demo.")

llm = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key=api_key,
    temperature=0.7,
    base_url="https://api.deepseek.com",
)

response = llm.invoke("你好！")
print(response.content)
