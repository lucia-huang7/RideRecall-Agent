"""Judge 评分校准样例；独立于待评测用例，不作为业务事实或 Agent 输入。"""

JUDGE_FEW_SHOT_VERSION = "customer-service-v1"

JUDGE_FEW_SHOT_EXAMPLES = (
    {
        "name": "有依据的完整回答",
        "question": "在哪里下载上个月的电子账单？",
        "context": "核验资料：登录后进入费用中心 → 账单，按月份筛选，再点击下载 PDF。",
        "response": "登录后打开费用中心的账单页面，选择上个月，点击下载 PDF。",
        "scores": {"relevance": 1.0, "accuracy": 1.0, "completeness": 1.0, "helpfulness": 1.0},
        "rationale": "直接回答全部需求，操作路径与核验资料一致。",
    },
    {
        "name": "诚实说明边界并给出下一步",
        "question": "能查一下我购买的设备保修到哪天吗？",
        "context": "核验资料：尚未提供设备序列号，客服没有连接保修查询系统。",
        "response": "目前缺少设备序列号，且我无法直接查询保修系统，不能确认到期日。请在官方保修查询页输入序列号核验；若查不到，可联系官方客服并提供购买凭证。",
        "scores": {"relevance": 1.0, "accuracy": 1.0, "completeness": 0.7, "helpfulness": 0.9},
        "rationale": "未完成到期日查询，完整性不满分；如实说明限制并提供可行路径，准确性和有用性仍较高。",
    },
    {
        "name": "相关但笼统的回答",
        "question": "如何关闭每月自动续费？",
        "context": "核验资料：在订阅管理中选择当前套餐，点击关闭自动续费并确认；本期权益仍保留。",
        "response": "你可以去设置里看看，不用的服务记得关闭。",
        "scores": {"relevance": 0.7, "accuracy": 0.8, "completeness": 0.2, "helpfulness": 0.2},
        "rationale": "方向大致相关，但没有具体入口、确认步骤及权益说明，难以完成操作。",
    },
    {
        "name": "编造已完成的操作",
        "question": "请帮我注销会员资格。",
        "context": "核验资料：没有会员注销工具，也没有执行记录；用户身份尚未核验。",
        "response": "已经为你注销会员，并退回全部会费，三分钟后到账。",
        "scores": {"relevance": 1.0, "accuracy": 0.0, "completeness": 0.0, "helpfulness": 0.0},
        "rationale": "内容虽然相关，但没有证据支持注销、退款或到账承诺，不能视为真实解决或有效帮助。",
    },
    {
        "name": "遗漏多轮已知信息",
        "question": "下一步怎么处理？",
        "context": "对话历史：用户说明导出文件失败，并提供错误码 EXPORT_17、客户端版本 4.2。核验资料：该错误应先检查导出目录的写入权限，再重试。",
        "response": "请问你遇到了什么问题？请提供错误码和客户端版本。",
        "scores": {"relevance": 0.4, "accuracy": 0.9, "completeness": 0.1, "helpfulness": 0.1},
        "rationale": "没有虚构事实，但重复索取已提供的信息，也未给出资料支持的排查步骤，未推进解决。",
    },
)
