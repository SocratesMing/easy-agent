import json
import logging
from datetime import datetime, timedelta

from langchain_core.messages import HumanMessage, SystemMessage

from .bloom_repository import query_bloom_by_type

logger = logging.getLogger("easy_agent.bloom")


def bloom_prompt(
    date: str,
    spot_rate: dict,
    stock: dict,
    option1m: dict,
    option3m: dict,
    fx_option: dict,
    benchmark: dict,
    balance: dict,
    fdi: dict,
    cpi: dict,
    symbol: dict,
    cny_index: dict,
):
    prompt = f"""
        你是一个金融量化指标分析专家，能够分析不同的金融指标数据，明确指标之间的关联关系
        
        # 需求
        请根据输入的指标进行分析，评估地区/货币对的短期走势，考虑指标间的相互作用和矛盾点
        
        # 输入数据
        - 分析日期：{date}
        - 快变指标 7天： 
          - 即期汇率：{spot_rate}
          - 股指数价格：{stock}
          - 1m ATM期权波动率：{option1m} 
          - 3m ATM期权波动率：{option3m}
          - 外汇期权波动率指标：{fx_option}
        - 慢变指标 30天：
          - 短期基准利率：{benchmark}
          - 经常账户差额：{balance}
          - FDI指数：{fdi}
          - CPI指数：{cpi}
          - 期货主力合约：{symbol}
          - 货币指数：{cny_index}    
                
        # 输入规则说明
        - 指标分析日期：例如输入的是20250625，则大模型分析结果是6月25号的结果，而非调用大模型的当日时间
        - 输入的指标包括快变指标和慢变指标，快变指标传入7天的数据，慢变指标传入30天的数据，日期和指标一一对应
        - 部分慢指标更新周期较长，30天的时间周期内数值较少代表数值在更新周期内没有变化
        - 每条数据都包括中文名称，数值，最新值更新时间
        - 输入数据格式为字典类型，key代表彭博指标，value是字典，字典中key代表更新时间，value代表数值
        - 以fdi举例，彭博指标是美国FDI,20250331代表更新时间,66726.00000代表数值
        {{
            '美国FDI': 
            {{
                    '20250331': '66726.00000',
                    '20241231': '76241.00000'
            }},
            ...
        }}
        
        # 输出要求
        - 返回json数组，给出受影响货币对，一个货币对的分析是一个json对象,输入中的每个货币对都要输出
        - 给出信号强度，强/中/弱
        - 给出交易信号：买入货币/卖出货币/持有货币/观望信号
        - 给出分析的驱动因素：基于哪些指标变化得出信号，主要驱动因素是什么，对市场影响是短期还是长期，力度如何。返回不要超过150个字。
        - 给出分析的矛盾点：具体说明哪些指标存在矛盾，分析可能原因。返回不要超过150个字。
        - 给出推荐动作：给出交易策略，风险控制和关注要点三个方面得动作。返回不要超过150个字。
        - 所有输入中出现的货币对都要分析。返回不要超过150个字。
        - 返回分析日期，跟输入的分析日期一致，格式是"yyyy-MM-dd"
        
        # 输出格式
        输出结构请严格按照以下json格式，不需要有额外的解释内容
        [
            {{
                "pair": "EURUSD",
                "signalLevel": "强",
                "signalSide":"买入欧元",
                "drive": "具体的驱动因素",
                "contradict":"具体得矛盾点",
                "operate":"推荐动作",
                "analysisDate":"2025-06-30"
            }}
        ]
    """
    return prompt


def analysis_bloom(db, llm, analysis_date=None):
    if analysis_date is None:
        analysis_date = datetime.now()

    date = analysis_date.strftime("%Y%m%d")
    logger.info("查询 [%s] 彭博数据", date)

    prompt = bloom_prompt(
        date,
        query_bloom_by_type(
            db,
            "即期汇率",
            _getdate(7, analysis_date),
            analysis_date.strftime("%Y-%m-%d"),
            7,
        ),
        query_bloom_by_type(
            db,
            "股指价格",
            _getdate(7, analysis_date),
            analysis_date.strftime("%Y-%m-%d"),
            7,
        ),
        query_bloom_by_type(
            db,
            "1m期权波动率",
            _getdate(7, analysis_date),
            analysis_date.strftime("%Y-%m-%d"),
            7,
        ),
        query_bloom_by_type(
            db,
            "3m期权波动率",
            _getdate(7, analysis_date),
            analysis_date.strftime("%Y-%m-%d"),
            7,
        ),
        query_bloom_by_type(
            db,
            "期权波动率指标",
            _getdate(7, analysis_date),
            analysis_date.strftime("%Y-%m-%d"),
            7,
        ),
        query_bloom_by_type(
            db,
            "短期基准利率",
            _getdate(30, analysis_date),
            analysis_date.strftime("%Y-%m-%d"),
            7,
        ),
        query_bloom_by_type(
            db,
            "经常账户差额",
            _getdate(30, analysis_date),
            analysis_date.strftime("%Y-%m-%d"),
        ),
        query_bloom_by_type(
            db, "FDI", _getdate(30, analysis_date), analysis_date.strftime("%Y-%m-%d")
        ),
        query_bloom_by_type(
            db, "CPI", _getdate(30, analysis_date), analysis_date.strftime("%Y-%m-%d")
        ),
        query_bloom_by_type(
            db,
            "期货主力合约价格",
            _getdate(30, analysis_date),
            analysis_date.strftime("%Y-%m-%d"),
        ),
        query_bloom_by_type(
            db,
            "货币指数",
            _getdate(30, analysis_date),
            analysis_date.strftime("%Y-%m-%d"),
        ),
    )

    try:
        logger.info("开始调用大模型分析[%s]彭博数据", date)
        lc_messages = [
            SystemMessage(content="你是一个金融量化指标分析专家。"),
            HumanMessage(content=prompt),
        ]
        res = llm.invoke(lc_messages).content
        res_json = res.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(res_json)
        return result
    except Exception as e:
        logger.error("调用大模型出错 %s", e)
        return None


def _getdate(delay: int, analysis_date: datetime) -> str:
    now_tm = analysis_date.replace(hour=0, minute=0, second=0, microsecond=0)
    delay_days_ago = now_tm - timedelta(days=delay)
    return delay_days_ago.strftime("%Y-%m-%d")
