from fastapi import FastAPI

app = FastAPI(title="SignalBench")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
