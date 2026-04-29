# 测试文件 可删除

from pymilvus import MilvusClient

# 使用你在 .env 里配置的地址
client = MilvusClient(uri="http://localhost:19530")

try:
    # 尝试列出所有集合
    collections = client.list_collections()
    print("✅ 成功！Milvus 已连接。")
    print(f"当前数据库里的集合有: {collections}")
except Exception as e:
    print(f"❌ 失败: {e}")