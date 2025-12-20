"""
简单的客户端脚本，用于向TokenProducer socket服务发送请求更新request_rate。
"""

import socket
import json
import argparse
import sys

def update_request_rate(host, port, force_reset, new_rate):
    """向TokenProducer socket服务发送新的请求速率"""
    try:
        # 创建socket连接
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.settimeout(5.0)  # 设置5秒超时
        client_socket.connect((host, port))

        # 准备请求数据
        request = {
            "request_rate": new_rate,
            "force_reset": force_reset,
        }
        request_data = json.dumps(request).encode('utf-8')

        # 发送请求
        client_socket.send(request_data)

        # 接收响应
        response_data = client_socket.recv(1024)
        response = json.loads(response_data.decode('utf-8'))

        client_socket.close()
        return response

    except socket.timeout:
        return {"status": "error", "message": "Connection timeout"}
    except ConnectionRefusedError:
        return {"status": "error", "message": "Connection refused. Is the TokenProducer server running?"}
    except Exception as e:
        return {"status": "error", "message": f"Error: {str(e)}"}

def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='Update TokenProducer request rate via socket')
    parser.add_argument('--host', default='localhost', help='TokenProducer server host (default: localhost)')
    parser.add_argument('--port', type=int, default=8888, help='TokenProducer server port (default: 8888)')
    parser.add_argument('--force-reset', help="Whether to clean the accumulated tokens", action='store_true', default=False)
    parser.add_argument('rate', type=float, help='New request rate (RPS)')

    args = parser.parse_args()

    # 验证速率值
    if args.rate < 0:
        print("错误: 请求速率必须是非负数")
        sys.exit(1)

    # 发送请求
    print(f"向 {args.host}:{args.port} 发送请求，更新速率为 {args.rate} RPS...")
    response = update_request_rate(args.host, args.port, args.force_reset, args.rate)

    # 显示响应
    if response['status'] == 'success':
        print(f"✅ 成功: {response['message']}")
    else:
        print(f"❌ 错误: {response['message']}")
        sys.exit(1)

if __name__ == "__main__":
    main()