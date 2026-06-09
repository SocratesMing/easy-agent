# plan 文件夹中的 plan.json 文件格式如下:
该json格式主要是用来配置策略的回测参数，包括回测时间、回测资金、回测运行参数等。
```json
{
  "config": {
    "indicator": [],
    "trade_delay": 10,
    "bizType": "FX",
    "optimize": {
      "param": [
        []
      ]
    },
    "init_money": 10000000,
    "startTime": "2023-05-01 15:26:00",
    "commission_rate": "0",
    "endTime": "2023-09-01 15:26:00",
    "sample_frequency": 0,
    "check": 1,
    "subscribed_data": [
      {
        "symbol": "EURUSDSP",
        "sub_type": "1",
        "kind": "tick",
        "sampling": "",
        "source": "EDATA_HO",
        "type": "FXSPOT"
      },
      {
        "symbol": "EURUSDSP",
        "sub_type": "2",
        "kind": "bar",
        "sampling": "",
        "source": "EDATA_HO",
        "type": "1H_BAR_DEPTH"
      }
    ],
    "trade_probability": 100
  },
  "custParams": [
    [
      {
        "name": "symbol",
        "attribute": "market",
        "value": "EURUSDSP",
        "key": "策略合约"
      },
      {
        "name": "source",
        "attribute": "market",
        "value": "EDATA_HO",
        "key": "合约渠道"
      },
      {
        "name": "bar_frequency",
        "attribute": "market",
        "value": "1H_BAR_DEPTH",
        "key": "bar数据频率"
      },
      {
        "name": "lot",
        "attribute": "risk",
        "value": 1000000,
        "key": "下单金额"
      },
      {
        "name": "tp",
        "attribute": "market",
        "value": 1200,
        "key": "止盈点数"
      },
      {
        "name": "sl",
        "attribute": "market",
        "value": 200,
        "key": "止损点数"
      },
      {
        "name": "sp",
        "attribute": "market",
        "value": 50,
        "key": "价差点数"
      },
      {
        "name": "start_month",
        "attribute": "market",
        "value": 0,
        "key": "开始交易月份"
      },
      {
        "name": "end_month",
        "attribute": "market",
        "value": 11,
        "key": "结束交易月份"
      },
      {
        "name": "start_hour",
        "attribute": "market",
        "value": 0,
        "key": "开始交易小时"
      },
      {
        "name": "end_hour",
        "attribute": "market",
        "value": 17,
        "key": "结束交易小时"
      },
      {
        "name": "ma_period",
        "attribute": "market",
        "value": 100,
        "key": "MA周期"
      },
      {
        "name": "ma_period_dispoint",
        "attribute": "market",
        "value": 200,
        "key": "MA偏离度"
      },
      {
        "name": "max_order_num",
        "attribute": "risk",
        "value": 1,
        "key": "最大挂单量"
      }
    ]
  ]
}
```
字段说明和可选参数如下：
bizType: FX，默认值为 FX
startTime: 回测开始时间
endTime: 回测结束时间
subscribed_data:回测订阅行情的数据，默认值为空数组
 - sub_type: 订阅类型，默认值为 1，可选值为 1、2
 - kind: sub_type为1时值为 tick，sub_type为2时值为 bar
 - source: 数据来源，默认值为 EDATA_HO
 - sampling: 默认为空字符
 - type: 数据类型，sub_type为1时可选择值为 FXSPOT、FXFWD，sub_type为2时可选择值为1N_BAR_DEPTH15N_BAR_DEPTH、30N_BAR_DEPTH、1H_BAR_DEPTH、4H_BAR_DEPTH

custParams: 自定义参数，默认值为空数组，每个元素为一个参数对象，包含以下字段：
 - name: 参数名称
 - attribute: 参数属性
 - value: 参数值
 - key: 参数显示名称

# .env 文件格式如下:
该文件位于策略根目录下，包括策略的说明等。
```
type=自营
tags=趋势, 震荡, 突破
desc=自营策略模板
BACKTEST_PLAN_TAG=default
```
type:取值为自营或做市，默认值为自营
tags:取值为 趋势, 震荡, 突破
desc:策略描述
BACKTEST_PLAN_TAG:当前使用的策略方案，默认为plan


# custom.json
参数属性文件，用于定义策略的参数属性。
```json
[
    {
        "ename": "symbol",
        "cname": "策略合约",
        "attribute": "market",
        "type": "symbol"
    },
    {
        "ename": "source",
        "cname": "合约渠道",
        "attribute": "market",
        "type": "source"
    },
    {
        "ename": "sl",
        "paramVal": "(5,0)",
        "cname": "止损点数",
        "attribute": "market",
        "type": "num"
    },
    {
        "ename": "tp",
        "paramVal": "(5,0)",
        "cname": "止盈点数",
        "attribute": "market",
        "type": "num"
    },
    
    ]

```
该文件的属性要与 plan.json 中的参数保持一致。
ename: 参数英文名称
paramVal: 参数值，type为num时该字段必填，"(5,2)"表示整数位为5个，小数位为2个
cname: 参数中文名称
attribute: 参数属性，可选值为 market、risk
type: 参数类型，str、num、bar_frequency