"""
本地测试 Claude 图表分析。
用法：python test_analysis.py <图片路径>
"""
import asyncio
import sys
import os

async def main(image_path: str):
    from app.adapters.claude_client import ClaudeClient
    from app.services.chart_analysis import ChartAnalysisService

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("❌ 请先设置环境变量 ANTHROPIC_API_KEY")
        sys.exit(1)

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    ext = image_path.rsplit(".", 1)[-1].lower()
    media_type = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"

    client = ClaudeClient(
        api_key=api_key,
        model="claude-sonnet-4-6",
        max_tokens=1024,
    )
    svc = ChartAnalysisService(client)

    print(f"📤 发送图片：{image_path}（{media_type}）\n")
    text, usage = await svc.analyze(image_bytes=image_bytes, symbol="TEST", media_type=media_type)

    print("=" * 60)
    print(text)
    print("=" * 60)
    print(f"\n📊 Token 用量")
    print(f"  input : {usage.input_tokens:,}")
    print(f"  output: {usage.output_tokens:,}")
    print(f"  cache_write: {usage.cache_creation_tokens:,}")
    print(f"  cache_read : {usage.cache_read_tokens:,}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python test_analysis.py <图片路径>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
