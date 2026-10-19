import multiprocessing


if __name__ == "__main__":
    multiprocessing.freeze_support()
    import uvicorn
    uvicorn.run("app.main:create_app", factory=True, host="127.0.0.1", port=8765, access_log=False)
