"""Start the single-worker, loopback-only EEG demonstration backend."""
import argparse
from pathlib import Path
import uvicorn
from backend.api import create_app

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1', choices=['127.0.0.1'])
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--data-dir', type=Path, default=None)
    args = parser.parse_args()
    uvicorn.run(create_app(data_dir=args.data_dir), host=args.host, port=args.port,
                workers=1, reload=False, access_log=False)
