# from langchain_deepseek import ChatDeepSeek

# llm = ChatDeepSeek(
#     model="deepseek-v4-flash",
#     temperature=0,
#     max_tokens=None,
#     timeout=None,
#     max_retries=2,
#     api_key="sk-2c6665b2900448b69c2c532638ae3d1d",
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


from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="deepseek-v4-flash",
    api_key="sk-2c6665b2900448b69c2c532638ae3d1d",  # 同样建议从环境变量读取
    temperature=0.7,
    base_url="https://api.deepseek.com",
)

response = llm.invoke("你好！")
print(response.content)
