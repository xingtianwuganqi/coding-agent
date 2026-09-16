from .model_api import call_model


def test():
    message = call_model(
        [{"role": "user", "content": "请只回复：连接成功"}],
        thinking=False,
    )
    print(message.content)


if __name__ == "__main__":
    test()
