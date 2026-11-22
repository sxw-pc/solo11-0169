# 汽车维修保修责任采证台

离线的车主授权、零件工序和保修返修采录台。车况诊断与车主签认确定准许施工的工序及费用；零件适配与独立试车决定交车；返厂症状、保修窗口和零件批次决定门店、供应商或车主的责任。三套账各有独立入口和持久化实体，原始 JSON 字节及 SHA256 入库，`audit` 从原始输入重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m autowarranty demo --db demo.sqlite
python -m autowarranty audit --db demo.sqlite
python -m pytest -q
```

CLI `vehicle-import`、`diagnosis-import`、`authorization-import`、`part-import`、`repair-import`、`inspection-import`、`warranty-import`、`return-import` 接受 `file --db state.sqlite`；`warranty-decide --db state.sqlite --case RET1 --at 2026-06-20T00:00:00Z` 追加责任裁定，`part-recall` 处理迟到召回，`report` 查询。正常/失败合成数据见 `fixtures`。真实车主签名、车载诊断设备和供应商质保网络不在本地首版范围。

## 跨登记地产权与拍卖交割

维修店回收待转卖车辆时，跨地区登记导出常晚于实车检验到达，且同一 VIN 可能同时属于真车的历史登记与套牌车的申报。`title-import`/`lien-import`/`frame-scan-import` 原样采入产权证、留置权与车架/发动机扫描；`factory-archive-import` 采入厂家生产档案，把车架号/发动机号锚定到首次上牌产权；`registry-doc-import` 采入登记机关的留置解除回执、吊销/更正与外部转移签收。`title-graph --vin VIN-1` 以不可替换的车架/发动机/厂家链为锚建立实体—产权—债权图：同号异车、OCR 误读、车架更换只保留为冲突候选，绝不按车主姓名或 VIN 字符串合并，因此套牌登记 TI2 的“无留置”洗不掉真车 TI1 上的 LI1，也不会误伤他车。

`title-prepare --vin VIN-1 --route payoff|assumption --at ...` 对同一实体与同一原始留置事实生成版本化的拍卖物权说明：`payoff` 路线须卖方结清 500000 分并取得登记解除回执后方可标“清洁产权”（买方不承接留置，价款归卖方）；`assumption` 路线凭银行与买方的三方承接文件可“带留置权”挂牌（买方取得受限产权，债权随车保留，价款与交割凭证不同）。待核、已核与已交割版本由 `title-statement` 分离返回。`delivery-sign` 签署买卖并阻断同一实体车的重复售卖；登记机关外部签收后若本地事务崩溃，`delivery-recover --receipt RCP-1` 按外部签收号幂等续写本地交割，已签买卖、已收价款与外部确认不因迟到纠错被抹除。`audit` 从原件与外部签收证据重放当前可售性与历史交割。
