/**
 * 天衍智能AI聊天应用
 * 支持图片上传、多轮对话和Markdown格式化
 */

document.addEventListener('DOMContentLoaded', () => {
    // DOM元素
    const messagesContainer = document.getElementById('chat-messages');
    const messageInput = document.getElementById('message-input');
    const sendButton = document.getElementById('send-button');
    const imageUpload = document.getElementById('image-upload');
    const docUpload = document.getElementById('doc-upload');
    const imagePreviewContainer = document.getElementById('image-preview-container');
    const imagePreview = document.getElementById('image-preview');
    const imageModal = document.getElementById('image-modal');
    const modalImage = document.getElementById('modal-image');
    const tokenCount = document.getElementById('token-count');
    const themeToggleButton = document.getElementById('theme-toggle-button');
    const loginBtn = document.getElementById('login-btn');
    const logoutBtn = document.getElementById('logout-btn');
    const historyHeader = document.querySelector('.history-header');
    const conversationList = document.getElementById('conversation-list');

    // 添加终止按钮
    const stopButton = document.createElement('button');
    stopButton.id = 'stop-button';
    stopButton.textContent = '终止';
    stopButton.title = '终止当前请求';
    stopButton.className = 'stop-button';
    stopButton.style.display = 'none'; // 初始隐藏
    sendButton.parentNode.insertBefore(stopButton, sendButton.nextSibling);

    // 添加清除历史按钮
    const clearHistoryBtn = document.createElement('button');
    clearHistoryBtn.innerHTML = '<i class="fas fa-trash"></i>';
    clearHistoryBtn.className = 'clear-history-btn';
    clearHistoryBtn.title = '清除对话历史';
    historyHeader.appendChild(clearHistoryBtn);

    // 添加新对话按钮
    const newChatBtn = document.createElement('button');
    newChatBtn.innerHTML = '<i class="fas fa-plus"></i>';
    newChatBtn.className = 'new-chat-btn';
    newChatBtn.title = '开始新对话';
    historyHeader.appendChild(newChatBtn);

    // 应用状态
    let currentImageFile = null;
    let isProcessing = false;
    let userTokens = 0;
    let assistantTokens = 0;
    const MAX_TOKENS = 1024;  // 设置每个用户的最大token限制为1024
    let totalSessionTokens = 0;  // 追踪整个会话的token总数
    let messageTimeout = null;  // 用于控制提示消息的显示时间
    let currentChatId = null;   // 当前聊天ID
    let chatHistories = [];     // 聊天历史记录列表

    // 当前请求的控制器
    let currentController = null;

    // 在页面加载时获取聊天历史列表并恢复会话历史
    loadChatHistoriesAndRestoreSession();

    // 保存原始登录按钮的事件处理程序引用
    function loginBtnHandler() {
        window.location.href = '/login';
    }

    // 退出登录处理
    function logoutBtnHandler() {
        if (confirm('确定要退出登录吗？')) {
            window.location.href = '/logout';
        }
    }

    /**
     * 检查用户登录状态并更新界面
     */
    function checkLoginStatus() {
        // 检查当前页面URL是否为index（意味着用户已登录）
        if (window.location.pathname.includes('/index')) {
            // 用户已登录，修改登录按钮为个人资料按钮
            if (loginBtn) {
                loginBtn.innerHTML = '<i class="fas fa-user-circle"></i> 个人资料';
                loginBtn.title = '查看个人资料';
                // 清除原有的点击事件
                loginBtn.onclick = null;
                // 设置新的点击事件
                loginBtn.addEventListener('click', () => {
                    window.location.href = '/profile';
                });
            }
            
            // 显示退出按钮
            if (logoutBtn) {
                logoutBtn.style.display = 'flex';
                logoutBtn.addEventListener('click', logoutBtnHandler);
            }
        } else {
            // 用户未登录，隐藏退出按钮
            if (logoutBtn) {
                logoutBtn.style.display = 'none';
            }
            // 设置登录按钮事件
            if (loginBtn) {
                loginBtn.addEventListener('click', loginBtnHandler);
            }
        }
    }

    /**
     * 加载聊天历史记录列表和当前会话
     */
    function loadChatHistoriesAndRestoreSession() {
        // 显示加载中提示
        appendSystemMessage('正在加载...');
        
        // 先获取聊天历史列表
        fetch('/chat-histories', {
            method: 'GET',
            credentials: 'same-origin'
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                chatHistories = data.histories || [];
                updateChatHistoryList();
                
                // 然后恢复当前会话
                restoreSessionHistory();
            } else {
                throw new Error(data.message || '加载聊天历史失败');
            }
        })
        .catch(error => {
            console.error('Error loading chat histories:', error);
            // 如果获取聊天历史失败，仍然尝试恢复当前会话
            restoreSessionHistory();
        });
    }

    /**
     * 页面加载时恢复会话历史
     */
    function restoreSessionHistory() {
        // 显示加载中提示
        appendSystemMessage('正在恢复会话历史...');
        
        // 从服务器获取会话历史
        fetch('/get-history', {
            method: 'GET',
            credentials: 'same-origin'
        })
        .then(response => response.json())
        .then(data => {
            // 清除加载提示
            messagesContainer.innerHTML = '';
            
            if (data.success) {
                // 如果有历史消息
                if (data.messages && data.messages.length > 0) {
                    // 跟踪我们是否需要跳过下一条消息（处理图片消息时可能会出现重复）
                    let skipNext = false;
                    
                    // 保存助手消息的位置，以便在后面添加token计数
                    let assistantMessagePositions = [];
                    
                    // 遍历并添加到界面
                    data.messages.forEach((msg, index) => {
                        // 如果需要跳过这条消息
                        if (skipNext) {
                            skipNext = false;
                            return;
                        }
                        
                        if (msg.role === 'user') {
                            // 检查是否包含图片
                            let imageSrc = null;
                            let textContent = msg.content;
                            
                            // 如果内容是数组（可能包含图片）
                            if (Array.isArray(msg.content)) {
                                // 提取文本内容
                                const textPart = msg.content.find(part => part.type === 'text');
                                textContent = textPart ? textPart.text : '';
                                
                                // 提取图片URL或路径
                                const imagePart = msg.content.find(part => part.type === 'image_url');
                                if (imagePart && imagePart.image_url) {
                                    // 这里处理图片URL或路径
                                    const imageUrl = imagePart.image_url.url;
                                    
                                    // 如果是数据URL，我们不显示，因为数据太大
                                    if (!imageUrl.startsWith('data:image')) {
                                        imageSrc = imageUrl;
                                    }
                                    
                                    // 检查下一条消息是否是同一个用户发送的消息（文本版本）
                                    if (index + 1 < data.messages.length && 
                                        data.messages[index + 1].role === 'user' && 
                                        typeof data.messages[index + 1].content === 'string') {
                                        // 标记跳过下一条消息，因为它只是为了兼容性而添加的文本版本
                                        skipNext = true;
                                    }
                                }
                            }
                            
                            // 在会话中查找可用的图片URL
                            if (!imageSrc && textContent.includes("请描述这张图片内容")) {
                                // 检查最近上传的图片路径
                                for (let i = index - 1; i >= 0; i--) {
                                    if (data.messages[i].role === 'user' && 
                                        Array.isArray(data.messages[i].content)) {
                                        const imgPart = data.messages[i].content.find(p => 
                                            p.type === 'image_url' && p.image_url);
                                        if (imgPart && imgPart.image_url.url) {
                                            imageSrc = imgPart.image_url.url;
                                            break;
                                        }
                                    }
                                }
                            }
                            
                            // 决定是否显示这条消息
                            if (textContent || imageSrc) {
                                appendMessage('user', textContent, imageSrc);
                            }
                        } else if (msg.role === 'assistant') {
                            appendMessage('assistant', msg.content);
                            // 记录助手消息位置，以便后续添加token计数
                            assistantMessagePositions.push(messagesContainer.children.length - 1);
                        } else if (msg.role === 'system') {
                            appendSystemMessage(msg.content);
                        }
                    });
                    
                    // 更新token计数
                    if (data.token_count) {
                        userTokens = data.token_count.user_tokens || 0;
                        assistantTokens = data.token_count.assistant_tokens || 0;
                        totalSessionTokens = data.token_count.total || 0;
                        
                        // 计算平均token使用量
                        const conversationPairs = assistantMessagePositions.length;
                        if (conversationPairs > 0) {
                            const avgUserTokens = Math.round(userTokens / conversationPairs);
                            const avgAssistantTokens = Math.round(assistantTokens / conversationPairs);
                            
                            // 添加一个总体token使用情况提示
                            const tokenMessage = document.createElement('div');
                            tokenMessage.className = 'token-count-message';
                            tokenMessage.innerHTML = `当前会话累计使用 ${userTokens} 用户tokens + ${assistantTokens} 助手tokens = ${totalSessionTokens} tokens`;
                            messagesContainer.appendChild(tokenMessage);
                        }
                        
                        updateTokenCount(userTokens, assistantTokens, totalSessionTokens);
                        checkTokenLimit(totalSessionTokens);
                    }
                    
                    // 只有当有消息时才显示已恢复的提示
                    if (messagesContainer.children.length > 0) {
                        appendSystemMessage('会话历史已恢复');
                    } else {
                        appendSystemMessage('开始新的对话吧');
                    }
                } else {
                    appendSystemMessage('欢迎使用天衍智能！开始新的对话吧');
                }
            } else {
                appendSystemMessage('无法恢复会话历史，开始新的对话');
            }
        })
        .catch(error => {
            console.error('Error restoring session:', error);
            messagesContainer.innerHTML = '';
            appendSystemMessage('恢复会话时出错，开始新的对话');
        });
    }

    // 检查用户是否已登录
    checkLoginStatus();

    // 添加清除历史记录的事件监听器
    clearHistoryBtn.addEventListener('click', clearHistory);

    // 添加新对话按钮事件处理
    newChatBtn.addEventListener('click', createNewChat);

    // 处理输入框的回车按键事件
    messageInput.addEventListener('keydown', handleEnterKey);
    
    // 自动调整输入框高度
    messageInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 150) + 'px';
    });

    // 发送按钮点击事件
    sendButton.addEventListener('click', sendMessage);
    
    // 图片上传事件
    imageUpload.addEventListener('change', handleImageUpload);
    
    // 文档上传处理（未实现功能）
    docUpload.addEventListener('change', () => {
        alert('文档上传功能即将推出，敬请期待！');
        docUpload.value = '';
    });
    
    /**
     * 处理按下Enter键发送消息
     * @param {KeyboardEvent} e - 键盘事件
     */
    function handleEnterKey(e) {
        // 按下Enter键且未按住Shift键时发送消息
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    }

    /**
     * 发送消息到服务器
     */
    function sendMessage() {
        // 获取用户输入的消息
        const messageText = messageInput.value.trim();
        
        // 如果没有文本消息且没有图片，则不发送
        if (!messageText && !currentImageFile) {
            // 显示提示消息
            showTempMessage('请输入消息或上传图片');
            return;
        }
        
        // 防止重复发送
        if (isProcessing) {
            return;
        }
        
        // 设置处理状态
        isProcessing = true;
        updateUIState();
        
        // 在界面上显示用户消息
        let imageSrc = null;
        if (currentImageFile) {
            // 从预览中获取图片URL
            const previewImg = imagePreview.querySelector('img');
            if (previewImg) {
                imageSrc = previewImg.src;
            }
        }
        appendMessage('user', messageText, imageSrc);
        
        // 显示"正在思考"提示
        const thinkingMessage = appendSystemMessage('AI正在思考...');
        
        // 创建一个新的AbortController
        currentController = new AbortController();
        const signal = currentController.signal;
        
        // 如果有图片，先上传图片
        if (currentImageFile && currentImageFile.path === undefined) {
            // 准备要发送的数据
            const formData = new FormData();
            formData.append('file', currentImageFile);
            
            // 上传图片
            fetch('/upload-image', {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
                signal: signal
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    console.log("图片上传成功:", data);
                    // 保存上传后的图片路径
                    currentImageFile = {
                        path: data.path
                    };
                    // 上传成功后，调用聊天API
                    callChatAPI(messageText, thinkingMessage, signal);
                } else {
                    throw new Error(data.message || '上传失败');
                }
            })
            .catch(error => {
                console.error('上传错误:', error);
                // 只有当不是用户主动终止的请求才显示错误
                if (!signal.aborted) {
                    // 移除"正在思考"提示
                    if (thinkingMessage && thinkingMessage.parentNode) {
                        thinkingMessage.parentNode.removeChild(thinkingMessage);
                    }
                    appendSystemMessage(`图片上传失败: ${error.message}`);
                    isProcessing = false;
                    updateUIState();
                }
            });
        } else {
            // 没有图片或图片已上传，直接调用聊天API
            callChatAPI(messageText, thinkingMessage, signal);
        }
        
        // 清空输入框
        messageInput.value = '';
        messageInput.style.height = 'auto';
    }
    
    /**
     * 调用聊天API
     * @param {string} messageText - 用户消息文本
     * @param {HTMLElement} thinkingMessage - "正在思考"提示元素
     * @param {AbortSignal} signal - 用于取消请求的信号
     */
    function callChatAPI(messageText, thinkingMessage, signal) {
        // 准备聊天请求的数据
        let requestData;
        
        if (currentImageFile && currentImageFile.path) {
            // 使用FormData发送带图片路径的请求
            const formData = new FormData();
            formData.append('message', messageText);
            formData.append('image_path', currentImageFile.path);
            
            if (currentChatId) {
                formData.append('chat_id', currentChatId);
            }
            
            // 发送带图片路径的请求
            fetch('/chat', {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
                signal: signal
            })
            .then(response => response.json())
            .then(data => handleResponse(data, thinkingMessage))
            .catch(error => handleFetchError(error, signal, thinkingMessage));
        } else {
            // 没有图片，使用JSON发送纯文本请求
            fetch('/chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    message: messageText,
                    chat_id: currentChatId
                }),
                credentials: 'same-origin',
                signal: signal
            })
            .then(response => response.json())
            .then(data => handleResponse(data, thinkingMessage))
            .catch(error => handleFetchError(error, signal, thinkingMessage));
        }
    }
    
    /**
     * 处理请求错误
     * @param {Error} error - 错误对象
     * @param {AbortSignal} signal - 中止信号
     * @param {HTMLElement} thinkingMessage - 思考提示元素
     */
    function handleFetchError(error, signal, thinkingMessage) {
        console.error('Error:', error);
        // 只有当不是用户主动终止的请求才显示错误
        if (!signal.aborted) {
            // 移除"正在思考"提示
            if (thinkingMessage && thinkingMessage.parentNode) {
                thinkingMessage.parentNode.removeChild(thinkingMessage);
            }
            
            // 显示错误消息
            let errorMessage = '';
            const errorMsg = error.message || '处理请求时出错';
            
            // 对不同类型的错误提供更友好的提示
            if (errorMsg.includes('Cannot read properties of undefined') || 
                errorMsg.includes('classList')) {
                errorMessage = "系统错误，请清除历史对话后重试";
            } else if (errorMsg.includes('Failed to fetch') || 
                     errorMsg.includes('NetworkError')) {
                errorMessage = "网络连接异常，请检查网络后重试";
            } else {
                errorMessage = "请求处理失败，请重试";
            }
            
            appendSystemMessage(errorMessage);
            isProcessing = false;
            updateUIState();
        }
    }

    /**
     * 终止当前请求
     */
    function stopRequest() {
        if (currentController) {
            currentController.abort();
            currentController = null;
            
            // 移除所有"AI正在思考"消息
            const thinkingMessages = document.querySelectorAll('.message.system');
            thinkingMessages.forEach(msg => {
                if (msg.textContent.includes('AI正在思考')) {
                    msg.parentNode.removeChild(msg);
                }
            });
            
            appendSystemMessage('请求已终止');
            isProcessing = false;
            updateUIState();
        }
    }

    /**
     * 处理服务器响应
     * @param {Object} data - 服务器返回的数据
     * @param {HTMLElement} thinkingMessage - "正在思考"提示元素
     */
    function handleResponse(data, thinkingMessage) {
        // 移除"正在思考"提示
        if (thinkingMessage && thinkingMessage.parentNode) {
            thinkingMessage.parentNode.removeChild(thinkingMessage);
        }
        
        // 确保没有其他"AI正在思考"消息存在
        const thinkingMessages = document.querySelectorAll('.message.system');
        thinkingMessages.forEach(msg => {
            if (msg.textContent.includes('AI正在思考')) {
                msg.parentNode.removeChild(msg);
            }
        });
        
        if (data.success) {
            // 添加AI回复
            appendMessage('assistant', data.response);
            
            // 更新token计数
            if (data.token_count) {
                userTokens = data.token_count.user_tokens;
                assistantTokens = data.token_count.assistant_tokens;
                totalSessionTokens = data.token_count.session_total;
                
                updateTokenCount(userTokens, assistantTokens, totalSessionTokens);
                checkTokenLimit(totalSessionTokens);
            }
            
            // 如果返回了聊天ID，更新当前聊天ID
            if (data.chat_id && !currentChatId) {
                currentChatId = data.chat_id;
                highlightCurrentChat();
            }
            
            // 如果token已达上限，显示系统消息并重新加载历史列表
            if (data.token_limit_reached) {
                appendSystemMessage(data.system_message || '已达到最大token限制，此对话已保存至历史记录。');
                
                // 如果返回了新的聊天ID，更新当前聊天ID
                if (data.new_chat_id) {
                    currentChatId = data.new_chat_id;
                }
                
                // 重新加载聊天历史列表
                fetch('/chat-histories', {
                    method: 'GET',
                    credentials: 'same-origin'
                })
                .then(response => response.json())
                .then(historyData => {
                    if (historyData.success) {
                        chatHistories = historyData.histories || [];
                        updateChatHistoryList();
                        highlightCurrentChat();
                    }
                })
                .catch(error => {
                    console.error('Error updating chat histories:', error);
                });
                
                // 清空消息容器，准备新的对话
                setTimeout(() => {
                    messagesContainer.innerHTML = '';
                    appendSystemMessage('开始新的对话');
                    
                    // 重置token计数
                    userTokens = 0;
                    assistantTokens = 0;
                    totalSessionTokens = 0;
                    updateTokenCount(userTokens, assistantTokens, totalSessionTokens);
                }, 3000);
            }
        } else {
            // 显示错误消息
            let errorMessage = '';
            const errorMsg = data.message || '处理请求时出错';
            
            // 对不同类型的错误提供更友好的提示
            if (errorMsg.includes('图片文件不存在') || errorMsg.includes('图片编码失败')) {
                errorMessage = "图片处理失败，请清除历史对话后重新上传";
            } else if (errorMsg.includes('Cannot read properties of undefined') || 
                      errorMsg.includes('classList')) {
                errorMessage = "系统错误，请清除历史对话后重试";
            } else if (errorMsg.includes('token')) {
                errorMessage = "会话已超出限制，请清除历史对话或创建新对话";
            } else {
                errorMessage = "请求处理失败，请重试";
            }
            
            appendSystemMessage(errorMessage);
        }
        
        // 清理图片信息
        if (currentImageFile) {
            clearImagePreview();
        }
        
        // 重置处理状态
        isProcessing = false;
        updateUIState();
    }

    /**
     * 向聊天添加消息
     * @param {string} role - 消息角色（user/assistant/system）
     * @param {string} text - 消息文本
     * @param {string|null} imageSrc - 可选的图片源
     */
    function appendMessage(role, text, imageSrc = null) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${role}`;
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        
        // 添加图片（如果有）
        if (imageSrc) {
            const imageContainer = document.createElement('div');
            const image = document.createElement('img');
            image.src = imageSrc;
            image.className = 'message-image';
            image.alt = '上传的图片';
            
            // 添加点击预览功能
            image.onclick = () => {
                modalImage.src = imageSrc;
                imageModal.classList.add('active');
            };
            
            imageContainer.appendChild(image);
            contentDiv.appendChild(imageContainer);
        }
        
        // 添加文本（如果有）
        if (text) {
            const textDiv = document.createElement('div');
            textDiv.className = 'message-text';
            
            // 所有消息都使用Markdown渲染
            textDiv.innerHTML = marked.parse(text);
            
            contentDiv.appendChild(textDiv);
        }
        
        messageDiv.appendChild(contentDiv);
        messagesContainer.appendChild(messageDiv);
        
        // 滚动到底部
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    /**
     * 处理图片上传
     * @param {Event} e - 上传事件
     */
    function handleImageUpload(e) {
        const file = e.target.files[0];
        if (!file) return;
        
        // 验证文件是否为图片
        if (!file.type.startsWith('image/')) {
            alert('请上传图片文件');
            imageUpload.value = '';
            return;
        }
        
        // 验证文件大小（限制为5MB）
        if (file.size > 5 * 1024 * 1024) {
            alert('图片文件大小不能超过5MB');
            imageUpload.value = '';
            return;
        }
        
        // 重置文件输入以允许相同文件再次上传
        imageUpload.value = '';
        
        // 保存当前文件
        currentImageFile = file;
        
        // 显示本地预览
        const reader = new FileReader();
        reader.onload = function(e) {
            // 清除现有预览
            imagePreview.innerHTML = '';
            
            // 创建预览图片
            const previewImg = document.createElement('img');
            previewImg.src = e.target.result;
            previewImg.alt = '预览图片';
            
            // 创建关闭按钮
            const closeBtn = document.createElement('button');
            closeBtn.className = 'close-image-btn';
            closeBtn.innerHTML = '×';
            closeBtn.title = '删除图片';
            closeBtn.addEventListener('click', (e) => {
                e.stopPropagation(); // 阻止事件冒泡
                clearImagePreview();
            });
            
            // 添加预览图片和关闭按钮
            imagePreview.appendChild(previewImg);
            imagePreview.appendChild(closeBtn);
            
            // 显示预览容器
            imagePreviewContainer.classList.add('active');
            
            // 添加点击预览功能
            previewImg.addEventListener('click', () => {
                modalImage.src = e.target.result;
                imageModal.classList.add('active');
            });
            
            // 提示用户可以发送图片了
            appendSystemMessage("图片已准备好，请输入问题或直接发送");
            messageInput.focus();
            messageInput.placeholder = "请描述您对这张图片的问题...";
        };
        reader.readAsDataURL(file);
    }
    
    /**
     * 隐藏图片预览但保留图片信息
     */
    function hideImagePreview() {
        // 只隐藏预览容器，但保留currentImageFile的信息
        imagePreviewContainer.classList.remove('active');
    }

    /**
     * 完全清除图片预览和信息
     */
    function clearImagePreview() {
        imagePreview.innerHTML = '';
        imagePreviewContainer.classList.remove('active');
        currentImageFile = null;
        messageInput.placeholder = "你想知道什么呢";  // 重置输入框提示
    }

    /**
     * 添加系统消息
     * @param {string} text - 系统消息文本
     */
    function appendSystemMessage(text) {
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message system';
        // 使用Markdown渲染系统消息
        messageDiv.innerHTML = marked.parse(text);
        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    /**
     * 生成图片显示URL
     * @param {string} path - 图片路径
     * @returns {string} - 显示URL
     */
    function getDisplayUrl(path) {
        // 如果路径是相对路径，转换为绝对URL
        if (path && path.startsWith('static/')) {
            return '/' + path;
        }
        return path;
    }

    /**
     * 更新Token计数显示
     * @param {number} userCount - 用户token数
     * @param {number} assistantCount - 助手token数
     * @param {number} conversationTokens - 当前对话token数
     */
    function updateTokenCount(userCount, assistantCount, conversationTokens) {
        // 更新header中的token显示 - 显示总session tokens与最大限制的对比
        tokenCount.innerHTML = `<span title="当前对话中用户tokens: ${userCount}, 助手tokens: ${assistantCount}">
            Tokens: ${totalSessionTokens}/${MAX_TOKENS}</span>`;
        
        // 根据token使用情况更新样式
        if (totalSessionTokens > MAX_TOKENS * 0.9) {
            // 如果超过90%的限制，显示红色警告
            tokenCount.classList.add('token-count-danger');
            tokenCount.classList.remove('token-count-warning');
        } else if (totalSessionTokens > MAX_TOKENS * 0.7) {
            // 如果超过70%的限制，显示黄色警告
            tokenCount.classList.add('token-count-warning');
            tokenCount.classList.remove('token-count-danger');
        } else {
            // 正常显示
            tokenCount.classList.remove('token-count-warning', 'token-count-danger');
        }
    }

    /**
     * 检查token限制
     * @param {number} currentTotal - 当前会话总token数
     */
    function checkTokenLimit(currentTotal) {
        if (currentTotal >= MAX_TOKENS) {
            // 已达到限制，仅添加系统消息提示，不禁用输入
            if (!document.querySelector('.token-limit-reached')) {
                const warningMessage = appendSystemMessage('已达到最大token限制 (1024)，请创建新对话继续交流。当前对话已自动保存到历史记录中。');
                warningMessage.classList.add('token-limit-reached');
            }
            
            // 将Token显示标记为危险状态
            tokenCount.classList.add('token-count-danger');
            tokenCount.classList.remove('token-count-warning');
        } else if (currentTotal > MAX_TOKENS * 0.9 && !document.querySelector('.token-limit-warning')) {
            // 接近限制（>90%）但尚未显示警告，显示警告消息
            const warningMessage = appendSystemMessage('警告：接近token限制，剩余可用token较少。');
            // 添加一个标记class，避免重复显示警告
            warningMessage.classList.add('token-limit-warning');
        }
    }

    /**
     * 更新UI状态
     */
    function updateUIState() {
        sendButton.disabled = isProcessing;
        messageInput.disabled = isProcessing;
        imageUpload.disabled = isProcessing;
        docUpload.disabled = isProcessing;
        
        // 更新终止按钮显示状态
        if (isProcessing) {
            sendButton.style.display = 'none';
            stopButton.style.display = 'inline-block';
        } else {
            sendButton.style.display = 'inline-block';
            stopButton.style.display = 'none';
            sendButton.textContent = '发送';
        }
    }

    // 设置模态图片查看器
    imageModal.addEventListener('click', (e) => {
        if (e.target === imageModal) {
            imageModal.classList.remove('active');
        }
    });
    
    // 初始状态更新
    updateUIState();
    
    // 检查用户是否已登录
    checkLoginStatus();
    
    // 初始化token计数显示
    tokenCount.innerHTML = `<span title="当前会话的Token使用情况">Tokens: 0/${MAX_TOKENS}</span>`;

    /**
     * 显示临时提示消息
     * @param {string} message - 提示消息文本
     * @param {number} duration - 显示时长(毫秒)，默认2秒
     */
    function showTempMessage(message, duration = 2000) {
        // 创建一个临时的系统消息
        const messageDiv = document.createElement('div');
        messageDiv.className = 'message system temp-message';
        // 使用Markdown渲染临时消息
        messageDiv.innerHTML = marked.parse(message);
        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
        
        // 如果已经有定时器，先清除
        if (messageTimeout) {
            clearTimeout(messageTimeout);
        }
        
        // 设置定时器，2秒后自动移除消息
        messageTimeout = setTimeout(() => {
            if (messageDiv && messageDiv.parentNode) {
                messageDiv.parentNode.removeChild(messageDiv);
            }
            messageTimeout = null;
        }, duration);
    }

    // 添加清除历史记录的功能
    function clearHistory() {
        // 弹出确认对话框
        if (confirm('确定要清除所有对话历史吗？此操作不可撤销。')) {
            // 清除服务器端会话
            fetch('/clear-history', {
                method: 'POST',
                credentials: 'same-origin'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // 清除页面显示的消息
                    messagesContainer.innerHTML = '';
                    
                    // 重置token计数
                    totalSessionTokens = 0;
                    userTokens = 0;
                    assistantTokens = 0;
                    updateTokenCount(0, 0, 0);
                    
                    // 显示已清除的通知
                    appendSystemMessage('对话历史已清除');
                } else {
                    throw new Error(data.message || '清除历史失败');
                }
            })
            .catch(error => {
                console.error('Error clearing history:', error);
                appendSystemMessage(`清除历史失败`);
            });
        }
    }

    // 添加新对话按钮事件处理
    function createNewChat() {
        if (isProcessing) return;
        
        isProcessing = true;
        updateUIState();
        
        fetch('/new-chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                title: '新对话 ' + new Date().toLocaleString('zh-CN', {
                    month: 'numeric',
                    day: 'numeric',
                    hour: 'numeric',
                    minute: 'numeric'
                })
            }),
            credentials: 'same-origin'
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // 清除现有消息
                messagesContainer.innerHTML = '';
                
                // 更新当前聊天ID
                currentChatId = data.chat_id;
                
                // 重置token计数
                userTokens = 0;
                assistantTokens = 0;
                totalSessionTokens = 0;
                updateTokenCount(0, 0, 0);
                
                // 获取最新的聊天历史列表
                fetch('/chat-histories', {
                    method: 'GET',
                    credentials: 'same-origin'
                })
                .then(response => response.json())
                .then(historyData => {
                    if (historyData.success) {
                        chatHistories = historyData.histories || [];
                        updateChatHistoryList();
                        highlightCurrentChat();
                    }
                })
                .catch(error => {
                    console.error('加载聊天历史失败:', error);
                });
                
                appendSystemMessage('已创建新对话，可以开始聊天了');
            } else {
                throw new Error(data.message || '创建新对话失败');
            }
            
            isProcessing = false;
            updateUIState();
        })
        .catch(error => {
            console.error('Error creating new chat:', error);
            appendSystemMessage(`创建新对话失败`);
            
            isProcessing = false;
            updateUIState();
        });
    }

    // 在这里调试工具，打印重要DOM元素确保它们被正确获取
    console.log("发送按钮:", sendButton);
    console.log("消息输入框:", messageInput);
    
    // 添加额外的事件监听器，确保发送功能正常工作
    console.log("重新绑定发送按钮事件");
    if(sendButton) {
        sendButton.onclick = function() {
            console.log("发送按钮被点击");
            sendMessage();
        };
    }

    // 主题切换初始化
    initTheme();
    
    /**
     * 初始化主题设置
     */
    function initTheme() {
        // 检查本地存储中的主题设置
        const savedTheme = localStorage.getItem('theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);
        
        // 更新主题图标
        updateThemeIcon(savedTheme);

        // 添加主题切换事件监听
        themeToggleButton.addEventListener('click', toggleTheme);
    }
    
    /**
     * 切换明暗主题
     */
    function toggleTheme() {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = currentTheme === 'light' ? 'dark' : 'light';
        
        // 设置新主题
        document.documentElement.setAttribute('data-theme', newTheme);
        localStorage.setItem('theme', newTheme);
        
        // 更新主题图标
        updateThemeIcon(newTheme);
    }
    
    /**
     * 更新主题图标
     * @param {string} theme - 当前主题
     */
    function updateThemeIcon(theme) {
        if (theme === 'dark') {
            themeToggleButton.innerHTML = '<i class="fas fa-sun"></i>';
        } else {
            themeToggleButton.innerHTML = '<i class="fas fa-moon"></i>';
        }
    }

    /**
     * 更新聊天历史列表
     */
    function updateChatHistoryList() {
        // 清空现有列表
        conversationList.innerHTML = '';
        
        // 如果没有历史记录，显示提示
        if (!chatHistories || chatHistories.length === 0) {
            const emptyMessage = document.createElement('div');
            emptyMessage.className = 'empty-history-message';
            emptyMessage.textContent = '无聊天历史记录';
            conversationList.appendChild(emptyMessage);
            return;
        }
        
        // 添加所有历史记录
        chatHistories.forEach(history => {
            const historyItem = document.createElement('div');
            historyItem.className = 'history-item';
            historyItem.dataset.id = history.id;
            if (currentChatId && parseInt(currentChatId) === history.id) {
                historyItem.classList.add('active');
            }
            
            // 创建标题元素，可点击加载该聊天记录
            const titleElement = document.createElement('div');
            titleElement.className = 'history-title';
            titleElement.textContent = history.title;
            titleElement.title = history.title;
            
            // 创建时间元素，显示创建时间
            const timeElement = document.createElement('div');
            timeElement.className = 'history-time';
            timeElement.textContent = formatDate(history.created_at);
            timeElement.title = history.created_at;
            
            // 创建删除按钮
            const deleteButton = document.createElement('button');
            deleteButton.className = 'history-delete';
            deleteButton.innerHTML = '<i class="fas fa-trash-alt"></i>';
            deleteButton.title = '删除此聊天历史';
            
            // 添加事件监听器
            historyItem.addEventListener('click', (e) => {
                // 如果点击的是删除按钮，不要触发加载聊天
                if (e.target.closest('.history-delete')) return;
                
                // 否则加载该聊天记录
                loadChatHistory(history.id);
            });
            
            deleteButton.addEventListener('click', (e) => {
                e.stopPropagation();  // 防止触发historyItem的点击事件
                if (confirm(`确定要删除聊天历史"${history.title}"吗？`)) {
                    deleteChatHistory(history.id);
                }
            });
            
            // 组装历史项
            historyItem.appendChild(titleElement);
            historyItem.appendChild(timeElement);
            historyItem.appendChild(deleteButton);
            
            // 添加到列表
            conversationList.appendChild(historyItem);
        });
    }

    /**
     * 高亮显示当前聊天
     */
    function highlightCurrentChat() {
        // 移除所有active类
        document.querySelectorAll('.history-item').forEach(item => {
            item.classList.remove('active');
        });
        
        // 如果有当前聊天ID，为其添加active类
        if (currentChatId) {
            const currentItem = document.querySelector(`.history-item[data-id="${currentChatId}"]`);
            if (currentItem) {
                currentItem.classList.add('active');
            }
        }
    }

    /**
     * 加载特定历史记录的聊天会话
     * @param {number} historyId - 聊天历史记录ID
     */
    function loadChatHistory(historyId) {
        if (isProcessing) return;
        
        isProcessing = true;
        updateUIState();
        
        // 清除现有消息
        messagesContainer.innerHTML = '';
        appendSystemMessage('正在加载聊天历史...');
        
        // 获取历史聊天记录
        fetch(`/chat-history/${historyId}`, {
            method: 'GET',
            credentials: 'same-origin'
        })
        .then(response => response.json())
        .then(data => {
            // 清除加载提示
            messagesContainer.innerHTML = '';
            
            if (data.success) {
                // 更新当前聊天ID
                currentChatId = historyId;
                highlightCurrentChat();
                
                // 如果有历史消息
                if (data.messages && data.messages.length > 0) {
                    // 跟踪我们是否需要跳过下一条消息（处理图片消息时可能会出现重复）
                    let skipNext = false;
                    
                    // 遍历并添加到界面
                    data.messages.forEach((msg, index) => {
                        // 如果需要跳过这条消息
                        if (skipNext) {
                            skipNext = false;
                            return;
                        }
                        
                        if (msg.role === 'user') {
                            // 检查是否包含图片
                            let imageSrc = null;
                            let textContent = msg.content;
                            
                            // 如果内容是数组（可能包含图片）
                            if (Array.isArray(msg.content)) {
                                // 提取文本内容
                                const textPart = msg.content.find(part => part.type === 'text');
                                textContent = textPart ? textPart.text : '';
                                
                                // 提取图片URL或路径
                                const imagePart = msg.content.find(part => part.type === 'image_url' || part.type === 'image');
                                if (imagePart) {
                                    if (imagePart.type === 'image_url' && imagePart.image_url) {
                                        imageSrc = imagePart.image_url.url;
                                    } else if (imagePart.type === 'image' && imagePart.image_path) {
                                        imageSrc = '/' + imagePart.image_path;
                                    }
                                }
                                
                                // 检查下一条消息是否是同一个用户发送的消息（文本版本）
                                if (index + 1 < data.messages.length && 
                                    data.messages[index + 1].role === 'user' && 
                                    typeof data.messages[index + 1].content === 'string') {
                                    // 标记跳过下一条消息，因为它只是为了兼容性而添加的文本版本
                                    skipNext = true;
                                }
                            }
                            
                            // 决定是否显示这条消息
                            if (textContent || imageSrc) {
                                appendMessage('user', textContent, imageSrc);
                            }
                        } else if (msg.role === 'assistant') {
                            appendMessage('assistant', msg.content);
                        } else if (msg.role === 'system') {
                            appendSystemMessage(msg.content);
                        }
                    });
                    
                    // 更新token计数
                    if (data.token_count) {
                        userTokens = data.token_count.user_tokens || 0;
                        assistantTokens = data.token_count.assistant_tokens || 0;
                        totalSessionTokens = data.token_count.total || 0;
                        
                        updateTokenCount(userTokens, assistantTokens, totalSessionTokens);
                        checkTokenLimit(totalSessionTokens);
                    }
                    
                    appendSystemMessage(`已加载聊天历史: ${data.history.title}`);
                } else {
                    appendSystemMessage(`已加载空的聊天历史: ${data.history.title}`);
                }
            } else {
                appendSystemMessage('无法加载聊天历史');
                currentChatId = null;
                highlightCurrentChat();
            }
            
            isProcessing = false;
            updateUIState();
        })
        .catch(error => {
            console.error('Error loading chat history:', error);
            messagesContainer.innerHTML = '';
            appendSystemMessage('加载聊天历史时出错');
            currentChatId = null;
            highlightCurrentChat();
            
            isProcessing = false;
            updateUIState();
        });
    }

    /**
     * 删除聊天历史记录
     * @param {number} historyId - 要删除的历史记录ID
     */
    function deleteChatHistory(historyId) {
        if (isProcessing) return;
        
        isProcessing = true;
        updateUIState();
        
        fetch(`/chat-history/${historyId}`, {
            method: 'DELETE',
            credentials: 'same-origin'
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // 从列表中移除
                chatHistories = chatHistories.filter(h => h.id !== historyId);
                updateChatHistoryList();
                
                // 如果删除的是当前聊天，清空聊天窗口
                if (currentChatId && parseInt(currentChatId) === historyId) {
                    messagesContainer.innerHTML = '';
                    appendSystemMessage('当前聊天历史已删除');
                    currentChatId = null;
                    
                    // 重置token计数
                    userTokens = 0;
                    assistantTokens = 0;
                    totalSessionTokens = 0;
                    updateTokenCount(0, 0, 0);
                }
                
                showTempMessage('聊天历史已删除');
            } else {
                throw new Error(data.message || '删除聊天历史失败');
            }
            
            isProcessing = false;
            updateUIState();
        })
        .catch(error => {
            console.error('Error deleting chat history:', error);
            showTempMessage(`删除失败`);
            
            isProcessing = false;
            updateUIState();
        });
    }

    /**
     * 格式化日期显示
     * @param {string} dateString - 日期字符串
     * @returns {string} 格式化后的日期
     */
    function formatDate(dateString) {
        const date = new Date(dateString);
        const now = new Date();
        
        // 获取今天的开始时间
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        
        // 获取昨天的开始时间
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);
        
        // 如果是今天
        if (date >= today) {
            return `今天 ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
        }
        // 如果是昨天
        else if (date >= yesterday) {
            return `昨天 ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
        }
        // 其他日期
        else {
            return `${date.getFullYear()}-${(date.getMonth() + 1).toString().padStart(2, '0')}-${date.getDate().toString().padStart(2, '0')}`;
        }
    }

    // 添加终止按钮的事件监听器
    stopButton.addEventListener('click', stopRequest);
}); 