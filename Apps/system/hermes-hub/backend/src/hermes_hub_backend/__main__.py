import uvicorn


def main() -> None:
    uvicorn.run(
        "hermes_hub_backend.main:create_app_from_env",
        factory=True,
        host="127.0.0.1",
        port=9120,
    )


if __name__ == "__main__":
    main()
