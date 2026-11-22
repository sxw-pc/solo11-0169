# 汽车维修保修责任采证台

离线的车主授权、零件工序和保修返修采录台。车况诊断与车主签认确定准许施工的工序及费用；零件适配与独立试车决定交车；返厂症状、保修窗口和零件批次决定门店、供应商或车主的责任。三套账各有独立入口和持久化实体，原始 JSON 字节及 SHA256 入库，`audit` 从原始输入重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m autowarranty demo --db demo.sqlite
python -m autowarranty audit --db demo.sqlite
python -m pytest -q
```

CLI `vehicle-import`、`diagnosis-import`、`authorization-import`、`part-import`、`repair-import`、`inspection-import`、`warranty-import`、`return-import` 接受 `file --db state.sqlite`；`warranty-decide --db state.sqlite --case RET1 --at 2026-06-20T00:00:00Z` 追加责任裁定，`part-recall` 处理迟到召回，`report` 查询。正常/失败合成数据见 `fixtures`。真实车主签名、车载诊断设备和供应商质保网络不在本地首版范围。

## 代步车逐次通行核销

代步车租约区分 `actual-toll`（逐笔实收）与 `fixed-daily`（固定每日通行包）。轨迹、路网版本、车辆标签交接、闸口签名、OCR 回执与租约版本分别入库，`loaner_tolls` 只在证据足够时把某笔通行转嫁给车主；客户车辆维修/保修状态完全不受影响。

```powershell
python -m autowarranty loan-agreement-import fixtures/loan-agreement-ok.json --db state.sqlite
python -m autowarranty road-network-import fixtures/road-network-tunnel.json --db state.sqlite
python -m autowarranty toll-fix-batch-import fixtures/toll-fix-batch.json --db state.sqlite
python -m autowarranty toll-receipt-import fixtures/toll-receipt-ok.json --db state.sqlite
python -m autowarranty toll-reconcile --db state.sqlite --at 2026-05-01T18:00:00Z
python -m autowarranty toll-settle --db state.sqlite --at 2026-05-04T00:00:00Z
python -m autowarranty toll-ledger --db state.sqlite
python -m autowarranty toll-explain --db state.sqlite --subject TR1
```

- 长定位缺口枚举所有满足时间/路网约束的候选路径，绝不把最快路径当作唯一事实；隧道与平行免费路在入口/出口坐标重合时保留两条候选。
- 只对具有可信闸口/标签身份的真实通行向车主计费；未签名 OCR 牌照、同号误识别申诉与标签转借只挂争议。
- 固定通行包按实际使用日先收日费，回执由门店向收费方核对，不叠加。
- 回执在重叠租约/借还交接前后只归属一次；可信票迟到或被临时封闭只追加补收、退款与争议，不改写原票或已收客户款。`audit` 可重放这些增量决策。
