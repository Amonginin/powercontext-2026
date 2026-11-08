"""在 Linux 上验证 seekdb 的 AI 函数修复（对着本机编译出的实例跑）。"""
import sys
try:
    import pymysql
except ImportError:
    print("需要 pymysql: pip3 install pymysql")
    sys.exit(1)

c = pymysql.connect(host='127.0.0.1', port=2881, user='root', password='', connect_timeout=15)
cur = c.cursor()
ok_count = 0
fail_count = 0

def run(sql, label, expect=None):
    global ok_count, fail_count
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        val = str(rows)
        good = True
        if expect is not None:
            good = expect in val
        if good:
            ok_count += 1
            print(f"OK   {label:34s} -> {val[:110]}")
        else:
            fail_count += 1
            print(f"WRONG {label:33s} -> {val[:110]}   (期望含 {expect})")
    except Exception as e:
        fail_count += 1
        print(f"ERR  {label:34s} -> {str(e)[:120]}")

M = "DBMS_AI_SERVICE"

# 1. ai_prompt 接受 JSON 参数
run("select ai_prompt('{0}/{0}/{1}', json_array(1,true), json_object('k',2))",
    "1 ai_prompt JSON 参数", '"args": [[1, true], {"k": 2}]')

# 2. ai_complete 的 config 为 null
run(f"call {M}.CREATE_AI_MODEL('m_c','{{\"type\":\"completion\",\"model_name\":\"qwen3-32b\"}}')", "  create completion model")
run(f"call {M}.CREATE_AI_MODEL_ENDPOINT('e_c','{{\"ai_model_name\":\"m_c\",\"scope\":\"all\",\"url\":\"http://127.0.0.1:8000/compatible-mode/v1/chat/completions\",\"access_key\":\"k\",\"provider\":\"openai\"}}')", "  create completion endpoint")
run("select length(ai_complete('m_c','hello',null)) > 0 as complete_ok", "2 ai_complete config=null", "(1,)")

# 3. ai_embed 指定维度
run(f"call {M}.CREATE_AI_MODEL('m_e','{{\"type\":\"dense_embedding\",\"model_name\":\"text-embedding-v1\"}}')", "  create embed model")
run(f"call {M}.CREATE_AI_MODEL_ENDPOINT('e_e','{{\"ai_model_name\":\"m_e\",\"scope\":\"all\",\"url\":\"http://127.0.0.1:8000/compatible-mode/v1/embeddings\",\"access_key\":\"k\",\"provider\":\"openai\"}}')", "  create embed endpoint")
run("select json_length(ai_embed('m_e','dimension-row',768)) as requested_dimension", "3 ai_embed 768 dim", "(768,)")

# 4. ai_rerank 传字符串数组，第四参数为 null
run(f"call {M}.CREATE_AI_MODEL('m_r','{{\"type\":\"rerank\",\"model_name\":\"bge-reranker\"}}')", "  create rerank model")
run(f"call {M}.CREATE_AI_MODEL_ENDPOINT('e_r','{{\"ai_model_name\":\"m_r\",\"scope\":\"all\",\"url\":\"http://127.0.0.1:8000/v1/rerank\",\"access_key\":\"k\",\"provider\":\"siliconflow\"}}')", "  create rerank endpoint")
run("select json_length(ai_rerank('m_r','apple','[\"apple\",\"banana\",\"fruit\",\"vegetable\"]',null)) as rerank_count", "4 ai_rerank string array", "(4,)")

# 5. Ollama provider，access_key 为空
run(f"call {M}.CREATE_AI_MODEL('m_o','{{\"type\":\"completion\",\"model_name\":\"qwen2.5\"}}')", "  create ollama model")
run(f"call {M}.CREATE_AI_MODEL_ENDPOINT('e_o','{{\"ai_model_name\":\"m_o\",\"scope\":\"all\",\"url\":\"http://127.0.0.1:8000/api/generate\",\"access_key\":\"\",\"provider\":\"ollama\"}}')", "5 ollama endpoint empty key")
run("select length(ai_complete('m_o','hello')) > 0 as ollama_complete_ok", "  ollama ai_complete", "(1,)")

run(f"call {M}.CREATE_AI_MODEL('m_oe','{{\"type\":\"dense_embedding\",\"model_name\":\"nomic-embed-text\"}}')", "  create ollama embed model")
run(f"call {M}.CREATE_AI_MODEL_ENDPOINT('e_oe','{{\"ai_model_name\":\"m_oe\",\"scope\":\"all\",\"url\":\"http://127.0.0.1:8000/api/embed\",\"access_key\":\"\",\"provider\":\"OLLAMA\"}}')", "  create ollama embed endpoint")
run("select json_valid(ai_embed('m_oe','hello')) as ollama_embed_ok", "  ollama ai_embed", "(1,)")

print()
print(f"通过 {ok_count}  失败 {fail_count}")
sys.exit(0 if fail_count == 0 else 1)
