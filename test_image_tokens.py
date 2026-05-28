"""
测试单张图片的 token 消耗。
用法：python test_image_tokens.py <图片路径>
"""
import sys
import base64
import anthropic

def test_image_tokens(image_path: str):
    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    client = anthropic.Anthropic()

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=16,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": "描述这张图片。"},
                ],
            }
        ],
    )

    usage = response.usage
    print(f"图片路径     : {image_path}")
    print(f"input_tokens : {usage.input_tokens}  (含图片+提示词)")
    print(f"output_tokens: {usage.output_tokens}")
    print(f"model        : {response.model}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python test_image_tokens.py <图片路径>")
        sys.exit(1)
    test_image_tokens(sys.argv[1])
