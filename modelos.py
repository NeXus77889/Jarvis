"""List models available to the configured Groq account without exposing credentials."""

from core.ai_provider import GroqAIProvider


def main() -> None:
    provider = GroqAIProvider()
    client = provider._get_client()
    for model in client.models.list().data:
        print(model.id)


if __name__ == "__main__":
    main()
