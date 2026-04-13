import requests
import csv

# Emby 服务器地址和 API 密钥
EMBY_SERVER_URL = None
API_KEY = None

OUTPUT_FILE = "emby_user_device.csv"  # 输出文件路径


def get_server_config():
    """获取 Emby 服务器配置"""
    global EMBY_SERVER_URL, API_KEY

    EMBY_SERVER_URL = input("请输入你的 Emby 服务器地址 (例如: http://localhost:8096): ").strip()
    API_KEY = input("请输入你的 Emby API 密钥: ").strip()

    # Basic validation
    if not EMBY_SERVER_URL or not API_KEY:
        raise ValueError("服务器地址和 API 密钥不能为空")

    # Remove trailing slash if present
    EMBY_SERVER_URL = EMBY_SERVER_URL.rstrip("/")


def fetch_users():
    """获取所有用户信息"""
    url = f"{EMBY_SERVER_URL}/Users"
    headers = {"accept": "application/json", "X-Emby-Token": API_KEY}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"获取用户信息时出错: {e}")
        return []


def fetch_devices():
    """获取所有设备信息"""
    url = f"{EMBY_SERVER_URL}/Devices"
    headers = {"accept": "application/json", "X-Emby-Token": API_KEY}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"获取设备信息时出错: {e}")
        return []


def count_devices_per_user(users, devices):
    """统计每个用户的设备数量"""
    user_device_data = []
    for user in users:
        user_id = user.get("Id")
        user_name = user.get("Name")
        # 过滤设备, 统计与当前用户关联的设备数量
        associated_devices = [
            device for device in devices.get("Items", []) if device.get("LastUserId") == user_id
        ]
        user_device_data.append({"user_name": user_name, "device_count": len(associated_devices)})
    return user_device_data


def save_to_csv(data, filename):
    """将统计结果保存到 CSV 文件"""
    try:
        with open(filename, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            # 写入表头
            writer.writerow(["用户名", "设备数量"])
            # 写入数据
            for item in data:
                writer.writerow([item["user_name"], item["device_count"]])
        print(f"统计结果已保存到文件: {filename}")
    except IOError as e:
        print(f"保存到文件时出错: {e}")


def main():
    """主函数"""
    try:
        get_server_config()
    except ValueError as e:
        print(f"配置错误: {e}")
        return

    print("正在获取用户信息...")
    users = fetch_users()
    if not users:
        print("未能获取到用户信息, 请检查服务器配置或网络连接。")
        return

    print("正在获取设备信息...")
    devices = fetch_devices()
    if not devices:
        print("未能获取设备信息, 请检查服务器配置或网络连接。")
        return

    print("正在统计每个用户的设备数量...")
    user_device_data = count_devices_per_user(users, devices)

    print("\n统计结果:")
    for item in user_device_data:
        print(f"用户: {item['user_name']}, 设备数量: {item['device_count']}")

    # 将结果保存到 CSV 文件
    save_to_csv(user_device_data, OUTPUT_FILE)


if __name__ == "__main__":
    main()
