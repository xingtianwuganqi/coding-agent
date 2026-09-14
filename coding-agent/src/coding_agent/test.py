from openai import OpenAI

client = OpenAI(
    api_key="sk-3cb0a38c5ece4d58a6941ddfdee85c5d",
    base_url="https://api.deepseek.com",
)

response = client.responses.create(
    model="deepseek-v4-flash",   
    instructions="You are a helpful coding assistant.",
    input="用一句话解释什么是agent"
)

print(response.output_text)
