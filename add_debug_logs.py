import sys

file_path = 'wukong_agent/web/service/__init__.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 在 line 145 (index 144) 的 "import re" 后添加调试日志
# 找到 "import re" 在 msg_type == 'ai' 块中的位置
for i, line in enumerate(lines):
    if i >= 140 and i <= 150 and 'import re' in line and 'msg_type' in lines[i-2]:
        # 在 import re 后添加空行和调试日志
        indent = '                            '
        debug_line = f"{indent}# 调试：打印消息结构\n{indent}logger.info(f'[{sid}] 🔍 AI消息 | content长度: {{len(content) if content else 0}} | 前100字符: {{str(content)[:100] if content else \"None\"}}')\n"
        lines.insert(i + 1, '\n')
        lines.insert(i + 2, debug_line)
        print(f'✅ 在第 {i+2} 行添加 AI 消息调试日志')
        break

# 找到 matches = re.findall 行并添加调试日志
for i, line in enumerate(lines):
    if 'matches = re.findall(think_pattern, content' in line:
        indent = '                            '
        debug_line = f"\n{indent}logger.info(f'[{sid}] 🔍 思考标签匹配 | matches数量: {{len(matches)}}')\n"
        lines.insert(i + 1, debug_line)
        print(f'✅ 在第 {i+2} 行添加 matches 调试日志')
        break

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('✅ 调试日志添加完成')
