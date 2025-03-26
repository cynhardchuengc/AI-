"""
天衍智能AI聊天应用后端
支持用户登录注册和AI对话功能
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector
import os
import logging
import sys
import json
import time
import uuid
import re
import base64
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from openai import OpenAI
import tiktoken
from PIL import Image
from io import BytesIO
import math
import datetime
import traceback

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 获取API密钥
api_key = os.getenv("OPENAI_API_KEY")
api_base = os.getenv("OPENAI_API_BASE", "https://api.openai-hk.com/v1")

# 创建Flask应用
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "tianyan_ai_secret_key")

# MySQL配置
MYSQL_CONFIG = {
    'host': '192.168.1.184',
    'user': 'root',
    'password': 'root',
    'database': 'tiktoken_limit',
    'pool_name': 'tiktoken_pool',
    'pool_size': 10  # 连接池大小
}

# 初始化MySQL连接池
db_pool = None
try:
    import mysql.connector.pooling
    db_pool = mysql.connector.pooling.MySQLConnectionPool(**MYSQL_CONFIG)
    logger.info("MySQL连接池初始化成功")
except Exception as e:
    logger.error(f"MySQL连接池初始化失败: {e}")
    # 如果连接池初始化失败，后续将使用普通连接

# 初始化OpenAI客户端
client = OpenAI(
    api_key=api_key,
    base_url=api_base
)
logger.info(f"Using API base URL: {api_base}")

# 设置最大token数量
MAX_TOKENS = 8192  # 系统总限制
USER_MAX_TOKENS = 1024  # 每个用户的限制
MAX_RESPONSE_TOKENS = 500

# 验证码有效期（秒）
VERIFICATION_CODE_EXPIRE = 300  # 5分钟有效期

# 配置上传文件的存储路径
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB 限制

# 确保上传目录存在
upload_dir = os.path.join(app.root_path, UPLOAD_FOLDER)
if not os.path.exists(upload_dir):
    os.makedirs(upload_dir)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 初始化tiktoken编码器
encoder = tiktoken.encoding_for_model("gpt-4")

# MySQL数据库连接函数
def get_db_connection():
    """获取数据库连接，优先从连接池获取"""
    try:
        if db_pool:
            # 从连接池获取连接
            connection = db_pool.get_connection()
            return connection
        else:
            # 如果连接池不可用，使用普通连接
            connection = mysql.connector.connect(
                host=MYSQL_CONFIG['host'],
                user=MYSQL_CONFIG['user'],
                password=MYSQL_CONFIG['password'],
                database=MYSQL_CONFIG['database']
            )
            return connection
    except mysql.connector.Error as err:
        logger.error(f"数据库连接失败: {err}")
        raise

# 允许的文件类型检查
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# 初始化数据库
def init_db():
    """初始化数据库，创建必要的表"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 创建用户表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL UNIQUE,
                password VARCHAR(255) NOT NULL,
                phone VARCHAR(20) UNIQUE,
                email VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建验证码表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS verification_codes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                phone VARCHAR(20) NOT NULL,
                code VARCHAR(10) NOT NULL,
                code_type ENUM('login', 'register', 'reset') DEFAULT 'login',
                used BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        ''')
        
        # 创建聊天历史记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS chat_histories (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                title VARCHAR(255) NOT NULL,
                session_data LONGTEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        
        # 检查是否需要添加session_data列
        cursor.execute("SHOW COLUMNS FROM chat_histories LIKE 'session_data'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE chat_histories ADD COLUMN session_data LONGTEXT AFTER title")
            logger.info("已向chat_histories表添加session_data列")
        
        conn.commit()
        conn.close()
        
        logger.info("数据库初始化成功，所有表已创建")
        return True
    except Exception as e:
        logger.error(f"数据库初始化失败: {str(e)}")
        return False

# AI聊天相关函数
def count_tokens(text):
    """计算文本的token数量"""
    try:
        # 确保text是字符串
        if not isinstance(text, str):
            text = json.dumps(text)
        tokens = encoder.encode(text)
        return len(tokens)
    except Exception as e:
        logger.error(f"Error counting tokens: {str(e)}")
        return 0

def sanitize_messages(messages):
    """清理和标准化消息格式"""
    sanitized = []
    for msg in messages:
        if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
            sanitized.append({
                'role': msg['role'],
                'content': msg['content']
            })
    return sanitized

def manage_token_limit(messages):
    """管理消息的token数量，确保不超过限制"""
    try:
        messages = sanitize_messages(messages)
        messages_json = json.dumps(messages)
        current_tokens = count_tokens(messages_json)
        return current_tokens <= (MAX_TOKENS - MAX_RESPONSE_TOKENS), current_tokens
    except Exception as e:
        logger.error(f"Error managing token limit: {str(e)}")
        return True, 0

def get_gpt_response(messages):
    """获取GPT回复"""
    try:
        # 标准化消息格式
        messages = sanitize_messages(messages)
        
        # 确保消息列表不为空
        if not messages:
            messages = [{"role": "system", "content": "你好，我是天衍智能助手，请问有什么可以帮助你的？"}]
        
        # 计算用户消息token数
        user_messages = [msg for msg in messages if msg['role'] == 'user']
        user_tokens = sum(count_tokens(msg['content']) for msg in user_messages)
        
        # 计算助手消息token数（历史对话）
        assistant_messages = [msg for msg in messages if msg['role'] == 'assistant']
        assistant_tokens_history = sum(count_tokens(msg['content']) for msg in assistant_messages)
        
        logger.info("开始调用GPT API")
        logger.debug(f"发送的消息数量: {len(messages)}")
        
        start_time = time.time()
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=MAX_RESPONSE_TOKENS,
                timeout=30  # 添加30秒超时
            )
        except Exception as api_error:
            logger.error(f"GPT API调用失败: {str(api_error)}")
            # 检查是否是网络或超时错误
            if "timeout" in str(api_error).lower():
                error_msg = "API请求超时，请稍后重试"
            elif "connection" in str(api_error).lower():
                error_msg = "连接API服务器失败，请检查网络"
            else:
                error_msg = f"API调用失败: {str(api_error)}"
            return error_msg, user_tokens, 0
            
        end_time = time.time()
        logger.info(f"GPT API调用完成，耗时: {end_time - start_time:.2f}秒")
        
        # 获取助手回复
        answer = response.choices[0].message.content.strip()
        # 计算新的助手回复token数
        new_assistant_tokens = count_tokens(answer)
        total_assistant_tokens = assistant_tokens_history + new_assistant_tokens
        
        logger.info(f"Successfully processed text message: User tokens={user_tokens}, Assistant tokens={total_assistant_tokens}")
        return answer, user_tokens, total_assistant_tokens
        
    except Exception as e:
        logger.error(f"Error in get_gpt_response: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        error_msg = f"处理请求时发生错误: {str(e)}"
        return error_msg, 0, count_tokens(error_msg)

# 保存用户会话历史到数据库
def save_user_session(user_id, session_data, chat_history_id=None):
    """
    保存用户会话数据到数据库
    :param user_id: 用户ID
    :param session_data: 消息数组或包含消息数组的会话数据
    :param chat_history_id: 聊天历史记录ID（可选）
    :return: 保存是否成功
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # 如果没有提供聊天历史ID，查找用户的默认会话
        if not chat_history_id:
            cursor.execute(
                "SELECT id FROM chat_histories WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1",
                (user_id,)
            )
            result = cursor.fetchone()
            
            if result:
                chat_history_id = result['id']
            else:
                # 如果没有找到历史记录，创建一个新的
                title = "默认对话"
                cursor.execute(
                    "INSERT INTO chat_histories (user_id, title, created_at, updated_at) VALUES (%s, %s, NOW(), NOW())",
                    (user_id, title)
                )
                chat_history_id = cursor.lastrowid

        # 确保session_data是一个包含消息数组的字典，或直接是消息数组
        messages = session_data
        if isinstance(session_data, dict):
            # 如果是旧格式的会话数据，提取消息
            user_messages_key = f'messages_{user_id}'
            if user_messages_key in session_data:
                messages = session_data[user_messages_key]
        
        # 序列化消息数据
        session_json = json.dumps({
            'messages': messages,
            'token_count': {
                'user_tokens': sum(count_tokens(msg['content']) for msg in messages if msg['role'] == 'user' and isinstance(msg['content'], str)),
                'assistant_tokens': sum(count_tokens(msg['content']) for msg in messages if msg['role'] == 'assistant' and isinstance(msg['content'], str)),
                'total': sum(count_tokens(msg['content']) for msg in messages if isinstance(msg['content'], str))
            },
            'updated_at': datetime.datetime.now().isoformat()
        })

        # 更新聊天历史记录
        cursor.execute(
            "UPDATE chat_histories SET session_data = %s, updated_at = NOW() WHERE id = %s AND user_id = %s",
            (session_json, chat_history_id, user_id)
        )
        
        if cursor.rowcount == 0:
            logger.warning(f"未找到要更新的聊天历史记录 (ID: {chat_history_id}, 用户ID: {user_id})")
            conn.close()
            return False

        conn.commit()
        conn.close()
        logger.info(f"成功保存用户会话 (用户ID: {user_id}, 聊天历史ID: {chat_history_id})")
        return True
    except Exception as e:
        logger.error(f"保存用户会话失败: {str(e)}")
        return False

def load_user_session(user_id, chat_history_id=None):
    """
    从数据库加载用户会话数据
    :param user_id: 用户ID
    :param chat_history_id: 聊天历史记录ID（可选）
    :return: 会话数据
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # 如果没有提供聊天历史ID，查找用户的默认会话
        if not chat_history_id:
            cursor.execute(
                "SELECT id, session_data FROM chat_histories WHERE user_id = %s ORDER BY updated_at DESC LIMIT 1",
                (user_id,)
            )
        else:
            cursor.execute(
                "SELECT id, session_data FROM chat_histories WHERE id = %s AND user_id = %s",
                (chat_history_id, user_id)
            )

        result = cursor.fetchone()
        conn.close()

        if result and result['session_data']:
            try:
                session_data = json.loads(result['session_data'])
                return {
                    'success': True, 
                    'messages': session_data.get('messages', []),
                    'token_count': session_data.get('token_count', {}),
                    'chat_id': result['id']
                }
            except json.JSONDecodeError:
                logger.error(f"解析会话数据JSON失败 (聊天历史ID: {result['id']})")
                return {'success': False, 'message': '解析会话数据失败'}
        else:
            logger.warning(f"未找到用户会话 (用户ID: {user_id}, 聊天历史ID: {chat_history_id})")
            return {'success': False, 'message': '未找到会话数据'}
    except Exception as e:
        logger.error(f"加载用户会话失败: {str(e)}")
        return {'success': False, 'message': f'加载会话数据时出错: {str(e)}'}

# 创建新的聊天历史记录
def create_chat_history(user_id, title, session_data=None):
    """
    创建新的聊天历史记录
    :param user_id: 用户ID
    :param title: 聊天历史标题
    :param session_data: 会话数据（可以是消息数组或包含消息数组的字典）
    :return: 新创建的聊天历史ID，如果创建失败则返回None
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 确保title不为空
        if not title:
            title = "新对话"
            
        # 处理会话数据
        if session_data:
            # 确保session_data是JSON格式
            if isinstance(session_data, list):
                # 消息数组
                messages = session_data
                session_json = json.dumps({
                    'messages': messages,
                    'token_count': {
                        'user_tokens': sum(count_tokens(msg['content']) for msg in messages if msg['role'] == 'user' and isinstance(msg['content'], str)),
                        'assistant_tokens': sum(count_tokens(msg['content']) for msg in messages if msg['role'] == 'assistant' and isinstance(msg['content'], str)),
                        'total': sum(count_tokens(msg['content']) for msg in messages if isinstance(msg['content'], str))
                    },
                    'updated_at': datetime.datetime.now().isoformat()
                })
            elif isinstance(session_data, dict):
                # 含有messages_key的字典
                user_messages_key = f'messages_{user_id}'
                if user_messages_key in session_data:
                    messages = session_data[user_messages_key]
                    session_json = json.dumps({
                        'messages': messages,
                        'token_count': {
                            'user_tokens': sum(count_tokens(msg['content']) for msg in messages if msg['role'] == 'user' and isinstance(msg['content'], str)),
                            'assistant_tokens': sum(count_tokens(msg['content']) for msg in messages if msg['role'] == 'assistant' and isinstance(msg['content'], str)),
                            'total': sum(count_tokens(msg['content']) for msg in messages if isinstance(msg['content'], str))
                        },
                        'updated_at': datetime.datetime.now().isoformat()
                    })
                else:
                    # 已经是正确格式的字典
                    session_json = json.dumps(session_data)
            else:
                # 字符串或其他格式，直接使用
                session_json = session_data if isinstance(session_data, str) else json.dumps(session_data)
        else:
            # 空会话数据
            session_json = json.dumps({
                'messages': [],
                'token_count': {'user_tokens': 0, 'assistant_tokens': 0, 'total': 0},
                'updated_at': datetime.datetime.now().isoformat()
            })
        
        # 插入数据
        cursor.execute(
            "INSERT INTO chat_histories (user_id, title, session_data, created_at, updated_at) VALUES (%s, %s, %s, NOW(), NOW())",
            (user_id, title, session_json)
        )
        
        # 获取新插入的ID
        chat_history_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        logger.info(f"已为用户 {user_id} 创建新的聊天历史 (ID: {chat_history_id}, 标题: {title})")
        return chat_history_id
    except Exception as e:
        logger.error(f"创建聊天历史失败: {str(e)}")
        return None

# 获取用户的所有聊天历史记录
def get_chat_histories(user_id, limit=20):
    """获取用户的所有聊天历史记录，按更新时间倒序"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute('''
            SELECT id, title, created_at, updated_at
            FROM chat_histories
            WHERE user_id = %s AND is_active = 1
            ORDER BY updated_at DESC
            LIMIT %s
        ''', (user_id, limit))
        
        histories = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        logger.info(f"获取了用户 {user_id} 的 {len(histories)} 条聊天历史记录")
        return histories
    except Exception as e:
        logger.error(f"获取聊天历史记录失败: {str(e)}")
        return []

# 获取特定的聊天历史记录
def get_chat_history(chat_history_id):
    """获取特定的聊天历史记录"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute('''
            SELECT id, user_id, title, created_at, updated_at
            FROM chat_histories
            WHERE id = %s AND is_active = 1
        ''', (chat_history_id,))
        
        history = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if history:
            logger.info(f"获取了聊天历史记录 (ID: {chat_history_id})")
        else:
            logger.warning(f"未找到聊天历史记录 (ID: {chat_history_id})")
        
        return history
    except Exception as e:
        logger.error(f"获取聊天历史记录失败: {str(e)}")
        return None

# 更新聊天历史记录标题
def update_chat_history_title(chat_history_id, new_title):
    """更新聊天历史记录标题"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('UPDATE chat_histories SET title = %s WHERE id = %s', 
                      (new_title, chat_history_id))
        
        affected_rows = cursor.rowcount
        
        conn.commit()
        cursor.close()
        conn.close()
        
        if affected_rows > 0:
            logger.info(f"更新了聊天历史记录标题 (ID: {chat_history_id}, 新标题: {new_title})")
            return True
        else:
            logger.warning(f"未找到聊天历史记录 (ID: {chat_history_id})")
            return False
    except Exception as e:
        logger.error(f"更新聊天历史记录标题失败: {str(e)}")
        return False

# 删除聊天历史记录（软删除）
def delete_chat_history(chat_history_id):
    """删除聊天历史记录（软删除）"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('UPDATE chat_histories SET is_active = 0 WHERE id = %s', 
                      (chat_history_id,))
        
        affected_rows = cursor.rowcount
        
        conn.commit()
        cursor.close()
        conn.close()
        
        if affected_rows > 0:
            logger.info(f"删除了聊天历史记录 (ID: {chat_history_id})")
            return True
        else:
            logger.warning(f"未找到聊天历史记录 (ID: {chat_history_id})")
            return False
    except Exception as e:
        logger.error(f"删除聊天历史记录失败: {str(e)}")
        return False

# 初始化数据库
init_db()

@app.route('/')
def root():
    return redirect(url_for('login'))

# 用户认证相关路由
@app.route('/login', methods=['GET', 'POST'])
def login():
    try:
        if request.method == 'GET':
            logger.debug("访问登录页面")
            return render_template('login.html')
        
        data = request.get_json()
        username = data.get('username')  # 这里username可能是用户名或手机号
        password = data.get('password')
        verification_code = data.get('code')  # 获取验证码
        
        logger.debug(f"尝试登录: 用户名或手机号={username}")
        
        # 判断输入的是用户名还是手机号
        is_phone = re.match(r'^1[3-9]\d{9}$', username)
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        if is_phone and verification_code:
            # 通过手机号和验证码登录
            # 首先验证验证码是否有效
            valid_code = verify_code(username, verification_code)
            if not valid_code:
                return jsonify({'success': False, 'message': '验证码无效或已过期'})
            
            # 验证码有效，查询用户信息
            cursor.execute('SELECT id, username FROM users WHERE phone = %s', (username,))
            result = cursor.fetchone()
            
            if not result:
                # 用户不存在，验证码登录失败
                return jsonify({'success': False, 'message': '该手机号未注册'})
            
            # 标记验证码为已使用
            mark_code_used(username, verification_code)
            
        elif is_phone:
            # 通过手机号和密码登录
            cursor.execute('SELECT id, username, password_hash FROM users WHERE phone = %s', (username,))
            result = cursor.fetchone()
            if not result or not check_password_hash(result['password_hash'], password):
                cursor.close()
                conn.close()
                return jsonify({'success': False, 'message': '手机号或密码错误'})
                
        else:
            # 通过用户名和密码登录
            cursor.execute('SELECT id, username, password_hash FROM users WHERE username = %s', (username,))
            result = cursor.fetchone()
            if not result or not check_password_hash(result['password_hash'], password):
                cursor.close()
                conn.close()
                return jsonify({'success': False, 'message': '用户名或密码错误'})
        
        # 登录成功，设置会话
        user_id = result['id']
        session['user_id'] = user_id
        session['username'] = result['username']
        
        # 恢复用户之前的会话历史
        user_messages_key = f'messages_{user_id}'
        if user_messages_key not in session:
            # 尝试从数据库加载用户之前的会话历史
            session_data = load_user_session(user_id)
            if session_data and user_messages_key in session_data:
                session[user_messages_key] = session_data[user_messages_key]
                logger.info(f"用户 {session['username']} 的会话历史已从数据库恢复")
        
        # 更新最后登录时间
        try:
            cursor.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = %s', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"更新登录时间失败: {str(e)}")
        
        cursor.close()
        conn.close()
        
        logger.info(f"用户 {result['username']} 登录成功")
        return jsonify({'success': True, 'redirect': url_for('index')})
            
    except Exception as e:
        logger.error(f"登录过程发生错误: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

# 生成验证码
def generate_verification_code():
    """生成6位数字验证码"""
    import random
    return ''.join(random.choices('0123456789', k=6))

# 保存验证码到数据库
def save_verification_code(phone, code, code_type='login'):
    """保存验证码到数据库"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 设置过期时间
        expires_at = time.strftime('%Y-%m-%d %H:%M:%S', 
                                   time.localtime(time.time() + VERIFICATION_CODE_EXPIRE))
        
        # 先将该手机号的旧验证码标记为已使用
        cursor.execute('''
            UPDATE verification_codes 
            SET is_used = 1 
            WHERE phone = %s AND type = %s AND is_used = 0
        ''', (phone, code_type))
        
        # 插入新的验证码
        cursor.execute('''
            INSERT INTO verification_codes 
            (phone, code, expires_at, type) 
            VALUES (%s, %s, %s, %s)
        ''', (phone, code, expires_at, code_type))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"保存验证码失败: {str(e)}")
        return False

# 验证码是否有效
def verify_code(phone, code, code_type='login'):
    """验证码是否有效"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # 查询未使用且在有效期内的验证码
        cursor.execute('''
            SELECT * FROM verification_codes 
            WHERE phone = %s AND code = %s AND type = %s 
            AND is_used = 0 AND expires_at > NOW()
            ORDER BY created_at DESC LIMIT 1
        ''', (phone, code, code_type))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        return result is not None
    except Exception as e:
        logger.error(f"验证验证码失败: {str(e)}")
        return False

# 标记验证码为已使用
def mark_code_used(phone, code, code_type='login'):
    """标记验证码为已使用"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE verification_codes 
            SET is_used = 1 
            WHERE phone = %s AND code = %s AND type = %s AND is_used = 0
        ''', (phone, code, code_type))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"标记验证码已使用失败: {str(e)}")
        return False

# 发送验证码接口
@app.route('/send-code', methods=['POST'])
def send_verification_code():
    """发送验证码接口"""
    try:
        data = request.get_json()
        phone = data.get('phone')
        code_type = data.get('type', 'login')  # 默认为登录验证码
        
        # 验证手机号格式
        if not re.match(r'^1[3-9]\d{9}$', phone):
            return jsonify({'success': False, 'message': '请输入有效的手机号码'})
        
        # 如果是注册验证码，检查手机号是否已注册
        if code_type == 'register':
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute('SELECT id FROM users WHERE phone = %s', (phone,))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if result:
                return jsonify({'success': False, 'message': '该手机号已被注册'})
        
        # 如果是登录验证码，检查手机号是否已注册
        if code_type == 'login':
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute('SELECT id FROM users WHERE phone = %s', (phone,))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if not result:
                return jsonify({'success': False, 'message': '该手机号未注册'})
        
        # 生成6位验证码
        code = generate_verification_code()
        
        # 保存验证码到数据库
        if not save_verification_code(phone, code, code_type):
            return jsonify({'success': False, 'message': '发送验证码失败，请稍后重试'})
        
        # 实际项目中，这里应该调用短信发送API
        # 但在演示项目中，我们只记录日志
        logger.info(f"向手机号 {phone} 发送验证码: {code}, 类型: {code_type}")
        
        # 在实际API中，可以替换为下面的代码：
        # send_sms(phone, f"您的验证码是: {code}，5分钟内有效，请勿泄露给他人。")
        
        return jsonify({
            'success': True, 
            'message': '验证码已发送',
            'debug_code': code  # 仅用于测试，实际环境应去掉
        })
        
    except Exception as e:
        logger.error(f"发送验证码失败: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

@app.route('/register', methods=['GET', 'POST'])
def register():
    try:
        if request.method == 'GET':
            logger.debug("访问注册页面")
            return render_template('register.html')
        
        # 获取和记录表单数据
        if request.is_json:
            data = request.get_json()
        else:
            # 如果不是JSON格式，尝试从表单数据获取
            data = request.form.to_dict()
        
        logger.debug(f"注册请求数据: {data}")
        
        username = data.get('username')
        password = data.get('password')
        phone = data.get('phone', '')  # 获取电话号码
        verification_code = data.get('code') # 获取验证码
        email = data.get('email', '')  # 获取邮箱，默认为空字符串
        
        logger.info(f"尝试注册用户: username={username}, phone={phone}, email={email}")
        
        # 验证输入字段
        validation_result = validate_registration(username, password, phone)
        if not validation_result['valid']:
            logger.warning(f"注册验证失败: {validation_result['message']}")
            return jsonify({'success': False, 'message': validation_result['message']})
        
        # 验证验证码
        if not verify_code(phone, verification_code, 'register'):
            logger.warning(f"验证码验证失败，手机号: {phone}")
            return jsonify({'success': False, 'message': '验证码无效或已过期'})
        
        # 使用pbkdf2:sha256方法进行密码加密
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        
        # 数据库操作
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # 插入用户数据，包括电话号码
            insert_query = 'INSERT INTO users (username, phone, password_hash, email) VALUES (%s, %s, %s, %s)'
            insert_values = (username, phone, hashed_password, email)
            
            logger.debug(f"执行SQL: {insert_query} 参数: {insert_values}")
            cursor.execute(insert_query, insert_values)
            conn.commit()
            
            # 获取新注册用户的ID
            user_id = cursor.lastrowid
            logger.info(f"新用户注册成功: ID={user_id}, username={username}, phone={phone}")
            
            # 标记验证码为已使用
            mark_code_used(phone, verification_code, 'register')
            
            return jsonify({'success': True, 'message': '注册成功'})
            
        except mysql.connector.IntegrityError as e:
            if conn:
                conn.rollback()
            logger.warning(f"注册失败 - 数据完整性错误: {str(e)}")
            
            if "username" in str(e):
                return jsonify({'success': False, 'message': '用户名已存在'})
            elif "phone" in str(e):
                return jsonify({'success': False, 'message': '手机号已被注册'})
            else:
                return jsonify({'success': False, 'message': f'注册失败，请检查输入信息: {str(e)}'})
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"注册过程数据库操作失败: {str(e)}")
            return jsonify({'success': False, 'message': f'数据库操作失败: {str(e)}'}), 500
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    except Exception as e:
        logger.error(f"注册过程发生错误: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

def validate_registration(username, password, phone):
    """验证注册信息的复杂度"""
    # 验证用户名
    if not username:
        return {'valid': False, 'message': '用户名不能为空'}
    
    if len(username) < 4:
        return {'valid': False, 'message': '用户名长度不能少于4个字符'}
    
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        return {'valid': False, 'message': '用户名只能包含字母、数字和下划线'}
    
    # 验证密码
    if not password:
        return {'valid': False, 'message': '密码不能为空'}
    
    if len(password) < 8:
        return {'valid': False, 'message': '密码长度不能少于8个字符'}
    
    # 检查密码复杂度 - 必须包含字母和数字
    if not (re.search(r'[A-Za-z]', password) and re.search(r'[0-9]', password)):
        return {'valid': False, 'message': '密码必须包含字母和数字'}
    
    # 验证电话号码（必填项）
    if not phone:
        return {'valid': False, 'message': '手机号码不能为空'}
    
    if not re.match(r'^1[3-9]\d{9}$', phone):
        return {'valid': False, 'message': '请输入有效的手机号码'}
    
    return {'valid': True, 'message': '验证通过'}

@app.route('/index')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('profile.html')

@app.route('/logout')
def logout():
    try:
        if 'user_id' in session:
            user_id = session['user_id']
            user_messages_key = f'messages_{user_id}'
            
            # 在退出登录前保存用户会话历史
            if user_messages_key in session:
                # 创建包含会话数据的字典
                session_data = {
                    user_messages_key: session[user_messages_key]
                }
                # 保存到数据库
                save_user_session(user_id, session_data)
                logger.info(f"用户 {session['username']} 的会话历史已保存到数据库")
        
        # 清除会话
        session.clear()
        return redirect(url_for('login'))
    except Exception as e:
        logger.error(f"退出登录过程发生错误: {str(e)}")
        # 尽管出错，仍然尝试清除会话并返回登录页面
        session.clear()
        return redirect(url_for('login'))

# AI聊天相关路由
@app.route('/chat', methods=['POST'])
def chat():
    """处理聊天请求并响应"""
    try:
        # 检查用户是否登录
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        
        user_id = session['user_id']
        user_name = session.get('username', '用户')
        
        # 获取请求数据
        if request.is_json:
            data = request.get_json()
            user_message = data.get('message', '')
            chat_id = data.get('chat_id')
        else:
            user_message = request.form.get('message', '')
            chat_id = request.form.get('chat_id')
            image_path = request.form.get('image_path')
        
        # 验证消息
        if not user_message and 'image_path' not in request.form:
            return jsonify({'success': False, 'message': '消息不能为空'}), 400
        
        # 为每个用户创建独立的消息历史键
        user_messages_key = f'messages_{user_id}'
        
        # 初始化或获取当前用户的消息历史
        if chat_id:
            # 如果指定了聊天ID，尝试加载该历史记录
            result = load_user_session(user_id, chat_id)
            if result['success']:
                messages = result['messages']
                token_count = result.get('token_count', {})
                current_session_tokens = token_count.get('total', 0)
            else:
                # 如果加载失败，创建新的会话
                messages = []
                current_session_tokens = 0
        else:
            # 从会话中获取消息历史
            messages = session.get(user_messages_key, [])
            
            # 计算当前会话的token数量
            current_session_tokens = 0
            for msg in messages:
                if isinstance(msg['content'], list):
                    # 图像消息
                    for part in msg['content']:
                        if part['type'] == 'text':
                            current_session_tokens += count_tokens(part['text'])
                else:
                    # 纯文本消息
                    current_session_tokens += count_tokens(msg['content'])
        
        # 添加用户消息
        if 'image_path' in request.form and request.form['image_path']:
            # 记录原始图片路径
            original_image_path = request.form['image_path']
            logger.info(f"接收到图片路径: {original_image_path}")
            
            # 处理图片路径，确保它是有效的
            image_path = original_image_path
            
            # 如果是相对于static的路径，确保正确处理
            if image_path.startswith('static/'):
                # 已经是相对路径，直接使用
                logger.info(f"使用相对路径: {image_path}")
            elif image_path.startswith('/static/'):
                # 移除前导斜杠
                image_path = image_path[1:]
                logger.info(f"转换路径: {original_image_path} -> {image_path}")
            
            # 添加用户文本消息
            if user_message:
                messages.append({"role": "user", "content": user_message})
            
            # 调用Vision API处理图片
            try:
                logger.info(f"准备处理图片: {image_path}")
                
                # 检查图片文件是否存在
                abs_path = os.path.join(app.root_path, image_path)
                if not os.path.exists(abs_path):
                    logger.error(f"图片文件不存在: {abs_path}")
                    return jsonify({
                        'success': False, 
                        'message': f'图片文件不存在: {abs_path}，请重新上传'
                    }), 404
                
                # 直接调用Vision API
                ai_response = get_vision_response(image_path, user_message if user_message else "这张图片里有什么？")
                
                # 计算token
                user_tokens = (count_tokens(user_message) if user_message else 0) + 500  # 用户文本token + 预估图片token
                assistant_tokens = count_tokens(ai_response)
                
                logger.info(f"图片处理成功，生成回复长度: {len(ai_response)}")
            except Exception as e:
                logger.error(f"调用Vision API出错: {str(e)}")
                logger.error(f"错误详情: {traceback.format_exc()}")
                return jsonify({
                    'success': False, 
                    'message': f'处理图像时出错: {str(e)}'
                }), 500
        else:
            # 纯文本消息
            messages.append({"role": "user", "content": user_message})
            
            # 调用GPT API
            try:
                ai_response, user_tokens, assistant_tokens = get_gpt_response(messages)
            except Exception as e:
                logger.error(f"调用GPT API出错: {str(e)}")
                return jsonify({'success': False, 'message': f'调用AI服务时出错: {str(e)}'}), 500
                
        # 添加助手回复
        messages.append({"role": "assistant", "content": ai_response})
        
        # 重新计算token使用情况
        total_user_tokens = 0
        total_assistant_tokens = 0
        
        for msg in messages:
            if msg["role"] == "user":
                if isinstance(msg['content'], list):
                    # 图像消息
                    for part in msg['content']:
                        if part['type'] == 'text':
                            total_user_tokens += count_tokens(part['text'])
                else:
                    # 纯文本消息
                    total_user_tokens += count_tokens(msg['content'])
            elif msg["role"] == "assistant":
                total_assistant_tokens += count_tokens(msg['content'])
        
        # 会话总token数
        total_session_tokens = total_user_tokens + total_assistant_tokens
        
        # 检查token是否超出限制
        token_limit_reached = False
        system_message = None
        new_chat_id = None
        
        if total_session_tokens >= USER_MAX_TOKENS:
            token_limit_reached = True
            system_message = f"已达到会话token限制({USER_MAX_TOKENS})，本次对话已保存到历史记录。"
            
            # 保存当前会话到历史记录
            if chat_id:
                save_user_session(user_id, messages, chat_id)
            else:
                # 创建新的聊天历史记录
                title = user_message[:20] + "..." if len(user_message) > 20 else user_message
                chat_id = create_chat_history(user_id, title, messages)
                
            # 创建新的会话
            title = f"新对话 {datetime.datetime.now().strftime('%m-%d %H:%M')}"
            new_chat_id = create_chat_history(user_id, title)
            
        # 更新session中的消息
        session[user_messages_key] = messages
        
        # 保存会话到数据库
        if chat_id and not token_limit_reached:
            save_user_session(user_id, messages, chat_id)
        elif not chat_id and not token_limit_reached:
            # 如果没有聊天ID且未达到token限制，创建新的聊天历史
            title = user_message[:20] + "..." if len(user_message) > 20 else user_message
            chat_id = create_chat_history(user_id, title, messages)
        
        # 准备响应数据
        response_data = {
            'success': True,
            'response': ai_response,
            'token_count': {
                'user_tokens': total_user_tokens,
                'assistant_tokens': total_assistant_tokens,
                'session_total': total_session_tokens
            },
            'token_limit_reached': token_limit_reached,
        }
        
        # 如果有聊天ID，添加到响应中
        if chat_id:
            response_data['chat_id'] = chat_id
        
        # 如果token超限，添加系统消息和新的聊天ID
        if token_limit_reached:
            response_data['system_message'] = system_message
            if new_chat_id:
                response_data['new_chat_id'] = new_chat_id
        
        return jsonify(response_data)
    
    except Exception as e:
        logger.error(f"聊天处理出错: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

# 辅助函数：编码图片为base64
def encode_image(image_path, max_size=1024):
    """将图片编码为base64格式，并进行适当压缩"""
    try:
        if not os.path.exists(image_path):
            logger.error(f"图片文件不存在: {image_path}")
            raise FileNotFoundError(f"图片文件不存在: {image_path}")
            
        file_size = os.path.getsize(image_path)
        logger.info(f"原始文件大小: {file_size/1024:.2f} KB")
        
        if file_size > 20 * 1024 * 1024:  # 20MB限制
            logger.error(f"图片文件过大: {file_size} bytes")
            raise ValueError(f"图片文件过大，大小: {file_size} bytes")
        
        # 使用PIL库压缩图片
        try:
            with Image.open(image_path) as img:
                # 检查图片格式
                logger.info(f"图片格式: {img.format}, 模式: {img.mode}")
                
                # 记录原始尺寸
                original_width, original_height = img.size
                logger.info(f"原始图片尺寸: {original_width}x{original_height}")
                
                # 计算缩放比例
                if max(original_width, original_height) > max_size:
                    scale_ratio = max_size / max(original_width, original_height)
                    new_width = math.floor(original_width * scale_ratio)
                    new_height = math.floor(original_height * scale_ratio)
                    logger.info(f"压缩图片至: {new_width}x{new_height}")
                    
                    try:
                        img = img.resize((new_width, new_height), Image.LANCZOS)
                    except Exception as resize_error:
                        logger.warning(f"使用LANCZOS调整大小失败，尝试使用BICUBIC: {str(resize_error)}")
                        img = img.resize((new_width, new_height), Image.BICUBIC)
                
                # 将图片转换为JPEG格式并压缩
                buffer = BytesIO()
                
                # 不同格式的图片需要不同的处理
                if img.mode == 'RGBA':
                    # RGBA模式（带透明通道）转换为RGB
                    background = Image.new('RGB', img.size, (255, 255, 255))
                    background.paste(img, mask=img.split()[3])  # 使用alpha通道作为mask
                    background.save(buffer, format="JPEG", quality=85)
                else:
                    # 其他模式直接转换和保存
                    img.convert('RGB').save(buffer, format="JPEG", quality=85)
                    
                buffer.seek(0)
                image_bytes = buffer.read()
                
                # 检查压缩后的大小
                compressed_size = len(image_bytes)
                logger.info(f"压缩后图片大小: {compressed_size/1024:.2f} KB")
                
                # 如果仍然太大，再次压缩
                if compressed_size > 4 * 1024 * 1024:  # 如果超过4MB
                    logger.warning(f"压缩后图片仍然过大: {compressed_size} bytes，进行二次压缩")
                    buffer = BytesIO()
                    with Image.open(BytesIO(image_bytes)) as img2:
                        # 降低质量继续压缩
                        img2.save(buffer, format="JPEG", quality=65)
                    buffer.seek(0)
                    image_bytes = buffer.read()
                    logger.info(f"二次压缩后大小: {len(image_bytes)/1024:.2f} KB")
                
                base64_image = base64.b64encode(image_bytes).decode('utf-8')
                logger.info(f"图片编码后大小: {len(base64_image)/1024:.2f} KB")
                return base64_image
        except UnboundLocalError as ule:
            logger.error(f"图片处理过程中出现未绑定局部变量错误: {str(ule)}")
            raise ValueError(f"图片处理错误: {str(ule)}")
        except OSError as ose:
            logger.error(f"图片文件损坏或格式不支持: {str(ose)}")
            raise ValueError(f"图片文件损坏或格式不支持: {str(ose)}")
    except Exception as e:
        logger.error(f"图片编码失败: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        raise ValueError(f"图片编码失败: {str(e)}")

def get_vision_response(image_path, user_message=""):
    """使用Vision API处理图片"""
    try:
        logger.info(f"开始处理图片: {image_path}")
        
        # 确保图片路径处理正确
        if not image_path.startswith('/'):
            # 相对路径，转换为绝对路径
            abs_path = os.path.join(app.root_path, image_path)
        else:
            # 已经是绝对路径
            abs_path = image_path
            
        logger.info(f"转换后的绝对路径: {abs_path}")
        
        # 检查文件是否存在
        if not os.path.exists(abs_path):
            logger.error(f"图片文件不存在: {abs_path}")
            return f"图片文件不存在: {abs_path}，请重新上传"
            
        # 检查API密钥是否有效
        if not api_key:
            logger.error("OpenAI API密钥未设置")
            return "系统配置错误: API密钥未设置"
        
        # 读取并编码图片，压缩至1024像素以减少数据量
        try:
            base64_image = encode_image(abs_path, max_size=1024)
            logger.info("图片编码成功")
            logger.debug(f"编码后图片大小: {len(base64_image)} bytes")
        except Exception as encode_error:
            logger.error(f"图片编码失败: {str(encode_error)}")
            return f"图片编码失败: {str(encode_error)}"
        
        # 构建消息，简化请求格式
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_message if user_message else "这张图片里面有什么"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ]
        
        logger.info("准备调用Vision API")
        # 记录完整请求信息用于调试
        logger.debug(f"API请求URL: {api_base}")
        logger.debug(f"API请求模型: gpt-4o")
        
        try:
            # 简化API调用，移除复杂的重试逻辑
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=500,
                timeout=60
            )
            
            logger.info("Vision API调用成功")
            answer = response.choices[0].message.content
            logger.info(f"成功获取图片描述，长度: {len(answer)}")
            return answer
                
        except Exception as api_error:
            logger.error(f"Vision API调用失败: {str(api_error)}")
            return f"处理图片时发生错误: {str(api_error)}"
            
    except Exception as e:
        logger.error(f"图片处理失败: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        return f"处理图片时发生错误: {str(e)}"

@app.route('/upload-image', methods=['POST'])
def upload_image():
    """处理图片上传"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        
        logger.info("开始处理图片上传请求")
        
        # 检查是否有文件上传
        if 'file' not in request.files:
            logger.warning("没有文件被上传")
            return jsonify({'success': False, 'message': '没有文件被上传'}), 400
        
        file = request.files['file']
        if not file or file.filename == '':
            logger.warning("没有选择文件")
            return jsonify({'success': False, 'message': '没有选择文件'}), 400
        
        logger.info(f"接收到文件: {file.filename}")
        
        # 检查文件类型
        if not allowed_file(file.filename):
            logger.warning(f"不支持的文件类型: {file.filename}")
            return jsonify({
                'success': False,
                'message': f'只支持以下格式: {", ".join(ALLOWED_EXTENSIONS)}'
            }), 400
        
        # 检查文件大小
        file_content = file.read()
        file.seek(0)  # 重置文件指针
        content_length = len(file_content)
        logger.info(f"文件大小: {content_length} bytes")
        
        if content_length > MAX_FILE_SIZE:
            logger.warning(f"文件过大: {content_length} bytes")
            return jsonify({
                'success': False,
                'message': f'文件大小不能超过 {MAX_FILE_SIZE/1024/1024}MB'
            }), 400
        
        # 确保上传目录存在
        upload_path = os.path.join(app.root_path, app.config['UPLOAD_FOLDER'])
        os.makedirs(upload_path, exist_ok=True)
        
        # 生成安全的文件名
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        file_path = os.path.join(upload_path, unique_filename)
        
        logger.info(f"准备保存文件到: {file_path}")
        
        # 保存文件
        try:
            file.save(file_path)
            if not os.path.exists(file_path):
                raise FileNotFoundError("文件保存失败")
            logger.info(f"文件成功保存: {file_path}")
        except Exception as e:
            logger.error(f"文件保存失败: {str(e)}")
            return jsonify({'success': False, 'message': f'文件保存失败: {str(e)}'}), 500
        
        # 返回文件信息
        relative_path = os.path.join('static', 'uploads', unique_filename)
        file_url = url_for('static', filename=f'uploads/{unique_filename}')
        
        logger.info(f"文件上传成功: {file_url}")
        return jsonify({
            'success': True,
            'message': '文件上传成功',
            'file_url': file_url,
            'path': relative_path
        })
        
    except Exception as e:
        logger.error(f"文件上传失败: {str(e)}")
        return jsonify({'success': False, 'message': f'文件上传失败: {str(e)}'}), 500

@app.route('/clear-history', methods=['POST'])
def clear_history():
    """清除当前会话的对话历史"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
            
        user_id = session['user_id']
        user_messages_key = f'messages_{user_id}'
        
        if user_messages_key in session:
            session.pop(user_messages_key)
            session.modified = True
            
            # 同时从数据库中清除会话历史
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM user_sessions WHERE user_id = %s', (user_id,))
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"用户 {session['username']} 清除了对话历史")
        return jsonify({'success': True, 'message': '会话历史已清除'})
    except Exception as e:
        logger.error(f"清除对话历史时发生错误: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

@app.route('/get-history', methods=['GET'])
def get_history():
    """获取当前会话的聊天历史"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
            
        user_id = session['user_id']
        user_messages_key = f'messages_{user_id}'
        
        # 检查会话中是否有消息历史
        if user_messages_key not in session or not session[user_messages_key]:
            return jsonify({
                'success': True,
                'messages': [],
                'token_count': {
                    'user_tokens': 0,
                    'assistant_tokens': 0,
                    'total': 0,
                    'limit': USER_MAX_TOKENS
                }
            })
        
        # 获取会话中的消息历史
        messages = session[user_messages_key]
        
        # 计算token使用情况
        # 提取文本消息以计算token
        text_messages = []
        for msg in messages:
            # 确保每条消息都有role和content
            if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
                continue
                
            # 如果content是一个字符串，直接添加
            if isinstance(msg['content'], str):
                text_messages.append(msg)
            # 如果content是一个列表（可能包含图片），提取文本部分
            elif isinstance(msg['content'], list):
                text_part = next((item for item in msg['content'] if item.get('type') == 'text'), None)
                if text_part:
                    # 创建一个新的消息对象，只包含文本内容
                    text_messages.append({
                        'role': msg['role'],
                        'content': text_part.get('text', '')
                    })
        
        # 计算用户消息和助手消息的token数
        user_messages = [msg for msg in text_messages if msg['role'] == 'user']
        user_tokens = sum(count_tokens(msg['content']) for msg in user_messages)
        
        assistant_messages = [msg for msg in text_messages if msg['role'] == 'assistant']
        assistant_tokens = sum(count_tokens(msg['content']) for msg in assistant_messages)
        
        total_tokens = user_tokens + assistant_tokens
        
        # 修改一下消息格式，避免前端处理base64编码的图片数据
        client_messages = []
        for msg in messages:
            if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                msg_copy = {
                    'role': msg['role'],
                    'content': msg['content']
                }
                
                # 如果content是列表，可能包含图片，需要处理一下
                if isinstance(msg_copy['content'], list):
                    # 为了界面展示，简化图片URL，防止返回大量base64数据
                    for item in msg_copy['content']:
                        if item.get('type') == 'image_url' and 'image_url' in item:
                            # 将base64图片URL替换为标记
                            if 'url' in item['image_url'] and item['image_url']['url'].startswith('data:image'):
                                item['image_url']['url'] = '(图片数据)'
                                
                client_messages.append(msg_copy)
                
                # 有些消息内容是原始数组，我们需要简化，防止重复显示
                if msg['role'] == 'user' and isinstance(msg['content'], list):
                    # 检查下一条消息是否是同一个用户的文本版本（我们在处理图片时会添加一个额外的文本消息）
                    next_index = messages.index(msg) + 1
                    if next_index < len(messages) and messages[next_index]['role'] == 'user' and isinstance(messages[next_index]['content'], str):
                        # 跳过这个额外添加的文本消息
                        continue
            else:
                client_messages.append(msg)  # 保留其他类型的消息
        
        # 返回消息历史和token计数
        return jsonify({
            'success': True,
            'messages': client_messages,
            'token_count': {
                'user_tokens': user_tokens,
                'assistant_tokens': assistant_tokens,
                'total': total_tokens,
                'limit': USER_MAX_TOKENS
            }
        })
    except Exception as e:
        logger.error(f"获取聊天历史时发生错误: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

# 获取聊天历史列表
@app.route('/chat-histories', methods=['GET'])
def get_user_chat_histories():
    """获取当前用户的所有聊天历史记录"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
            
        user_id = session['user_id']
        
        # 获取用户的聊天历史记录
        histories = get_chat_histories(user_id)
        
        # 格式化日期时间
        for history in histories:
            if 'created_at' in history and history['created_at']:
                history['created_at'] = history['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if 'updated_at' in history and history['updated_at']:
                history['updated_at'] = history['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify({
            'success': True,
            'histories': histories
        })
    except Exception as e:
        logger.error(f"获取聊天历史记录失败: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

# 获取特定聊天历史的对话内容
@app.route('/chat-history/<int:history_id>', methods=['GET'])
def get_history_chat(history_id):
    """获取特定聊天历史的对话内容"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
            
        user_id = session['user_id']
        
        # 检查聊天历史记录是否存在并属于当前用户
        history = get_chat_history(history_id)
        if not history:
            return jsonify({'success': False, 'message': '未找到聊天历史记录'}), 404
            
        if history['user_id'] != user_id:
            return jsonify({'success': False, 'message': '无权访问此聊天历史记录'}), 403
        
        # 从数据库加载会话数据
        session_data = load_user_session(user_id, history_id)
        
        if not session_data:
            return jsonify({
                'success': False,
                'message': '未找到会话数据'
            }), 404
        
        # 用户消息键
        user_messages_key = f'messages_{user_id}'
        
        if user_messages_key not in session_data or not session_data[user_messages_key]:
            return jsonify({
                'success': True,
                'messages': [],
                'history': history
            })
        
        # 处理消息内容，准备返回
        messages = session_data[user_messages_key]
        
        # 计算token使用情况
        # 提取文本消息以计算token
        text_messages = []
        for msg in messages:
            # 确保每条消息都有role和content
            if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
                continue
                
            # 如果content是一个字符串，直接添加
            if isinstance(msg['content'], str):
                text_messages.append(msg)
            # 如果content是一个列表（可能包含图片），提取文本部分
            elif isinstance(msg['content'], list):
                text_part = next((item for item in msg['content'] if item.get('type') == 'text'), None)
                if text_part:
                    # 创建一个新的消息对象，只包含文本内容
                    text_messages.append({
                        'role': msg['role'],
                        'content': text_part.get('text', '')
                    })
        
        # 计算用户消息和助手消息的token数
        user_messages = [msg for msg in text_messages if msg['role'] == 'user']
        user_tokens = sum(count_tokens(msg['content']) for msg in user_messages)
        
        assistant_messages = [msg for msg in text_messages if msg['role'] == 'assistant']
        assistant_tokens = sum(count_tokens(msg['content']) for msg in assistant_messages)
        
        total_tokens = user_tokens + assistant_tokens
        
        # 修改一下消息格式，避免前端处理base64编码的图片数据
        client_messages = []
        for msg in messages:
            if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                msg_copy = {
                    'role': msg['role'],
                    'content': msg['content']
                }
                
                # 如果content是列表，可能包含图片，需要处理一下
                if isinstance(msg_copy['content'], list):
                    # 为了界面展示，简化图片URL，防止返回大量base64数据
                    for item in msg_copy['content']:
                        if item.get('type') == 'image_url' and 'image_url' in item:
                            # 将base64图片URL替换为标记
                            if 'url' in item['image_url'] and item['image_url']['url'].startswith('data:image'):
                                item['image_url']['url'] = '(图片数据)'
                                
                client_messages.append(msg_copy)
            else:
                client_messages.append(msg)  # 保留其他类型的消息
        
        # 返回消息历史和token计数
        return jsonify({
            'success': True,
            'messages': client_messages,
            'history': history,
            'token_count': {
                'user_tokens': user_tokens,
                'assistant_tokens': assistant_tokens,
                'total': total_tokens,
                'limit': USER_MAX_TOKENS
            }
        })
    except Exception as e:
        logger.error(f"获取聊天历史内容失败: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

# 更新聊天历史标题
@app.route('/chat-history/<int:history_id>/title', methods=['PUT'])
def update_history_title(history_id):
    """更新聊天历史标题"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
            
        user_id = session['user_id']
        
        # 检查聊天历史记录是否存在并属于当前用户
        history = get_chat_history(history_id)
        if not history:
            return jsonify({'success': False, 'message': '未找到聊天历史记录'}), 404
            
        if history['user_id'] != user_id:
            return jsonify({'success': False, 'message': '无权修改此聊天历史记录'}), 403
        
        # 获取新标题
        data = request.get_json()
        if not data or 'title' not in data:
            return jsonify({'success': False, 'message': '缺少标题参数'}), 400
            
        new_title = data['title'].strip()
        if not new_title:
            return jsonify({'success': False, 'message': '标题不能为空'}), 400
        
        # 更新标题
        if update_chat_history_title(history_id, new_title):
            return jsonify({
                'success': True,
                'message': '标题已更新',
                'history_id': history_id,
                'new_title': new_title
            })
        else:
            return jsonify({'success': False, 'message': '更新标题失败'}), 500
    except Exception as e:
        logger.error(f"更新聊天历史标题失败: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

# 删除聊天历史记录
@app.route('/chat-history/<int:history_id>', methods=['DELETE'])
def delete_history(history_id):
    """删除聊天历史记录"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
            
        user_id = session['user_id']
        
        # 检查聊天历史记录是否存在并属于当前用户
        history = get_chat_history(history_id)
        if not history:
            return jsonify({'success': False, 'message': '未找到聊天历史记录'}), 404
            
        if history['user_id'] != user_id:
            return jsonify({'success': False, 'message': '无权删除此聊天历史记录'}), 403
        
        # 删除聊天历史记录
        if delete_chat_history(history_id):
            return jsonify({
                'success': True,
                'message': '聊天历史记录已删除',
                'history_id': history_id
            })
        else:
            return jsonify({'success': False, 'message': '删除聊天历史记录失败'}), 500
    except Exception as e:
        logger.error(f"删除聊天历史记录失败: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

# 创建新的聊天对话（开始新对话）
@app.route('/new-chat', methods=['POST'])
def new_chat():
    """创建新的聊天对话"""
    try:
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': '请先登录'}), 401
            
        user_id = session['user_id']
        
        # 获取标题，如果提供了的话
        data = request.get_json() or {}
        title = data.get('title', '新对话')
        
        # 为每个用户创建独立的消息历史键
        user_messages_key = f'messages_{user_id}'
        
        # 清空当前会话
        session[user_messages_key] = []
        
        # 创建新的聊天历史记录
        chat_history_id = create_chat_history(user_id, title)
        
        if chat_history_id:
            return jsonify({
                'success': True,
                'message': '新对话已创建',
                'chat_id': chat_history_id,
                'title': title
            })
        else:
            return jsonify({'success': False, 'message': '创建新对话失败'}), 500
    except Exception as e:
        logger.error(f"创建新对话失败: {str(e)}")
        return jsonify({'success': False, 'message': f'服务器内部错误: {str(e)}'}), 500

if __name__ == '__main__':
    # 默认使用不同的端口，避免冲突
    port = 5000
    logger.info(f"Starting server on port {port}")
    app.run(debug=True, port=port) 