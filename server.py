"""Start the local operator backend with optional paired LAN participant access."""
import argparse
from pathlib import Path
import uvicorn
from backend.api import create_app

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1', choices=['127.0.0.1'])
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--lan', action='store_true', help='Allow paired iPad participant browsers on this network')
    parser.add_argument('--data-dir', type=Path, default=None)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    app = create_app(data_dir=args.data_dir, lan=args.lan, port=args.port)
    print(f"工作人员界面: http://127.0.0.1:{args.port}/operator.html", flush=True)
    if args.lan:
        print("iPad 配对链接和二维码请在工作人员界面的连接面板查看。", flush=True)
        if not app.state.network.addresses:
            print("尚未找到局域网 IPv4 地址，请连接 Wi-Fi / 以太网后在工作台刷新地址。", flush=True)
    uvicorn.run(app, host='0.0.0.0' if args.lan else args.host, port=args.port,
                workers=1, reload=False, access_log=False, proxy_headers=False)
