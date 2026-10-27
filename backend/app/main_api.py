from fastapi import FastAPI


app = FastAPI(title="QuizArena API")


@app.get("/api/v1/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/v1/readyz")
async def readyz():
    # TODO: ping Postgres + Redis, lỗi thì trả 503
    return {"status": "ready"}
