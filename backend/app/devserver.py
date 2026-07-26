import asyncio

import uvicorn


def selector_loop_factory() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        loop="app.devserver:selector_loop_factory",
    )


if __name__ == "__main__":
    main()
