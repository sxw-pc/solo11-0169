# 汽车维修保修责任采证台

离线的车主授权、零件工序和保修返修采录台。车况诊断与车主签认确定准许施工的工序及费用；零件适配与独立试车决定交车；返厂症状、保修窗口和零件批次决定门店、供应商或车主的责任。三套账各有独立入口和持久化实体，原始 JSON 字节及 SHA256 入库，`audit` 从原始输入重放当前状态。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m autowarranty demo --db demo.sqlite
python -m autowarranty audit --db demo.sqlite
python -m pytest -q
```

CLI `vehicle-import`、`diagnosis-import`、`authorization-import`、`part-import`、`repair-import`、`inspection-import`、`warranty-import`、`return-import` 接受 `file --db state.sqlite`；`warranty-decide --db state.sqlite --case RET1 --at 2026-06-20T00:00:00Z` 追加责任裁定，`part-recall` 处理迟到召回，`report` 查询。正常/失败合成数据见 `fixtures`。真实车主签名、车载诊断设备和供应商质保网络不在本地首版范围。

## 多姿态相机标定与功能范围发布

四角靶板零像素残差只能证明画面中心的单应成立，约束不到外周径向畸变：针孔镜头与带径向畸变的镜头可以给出完全相同的中心四角，却在离中心 800 像素的车道边界上相差约 40 像素、折算 40 米前方约 0.6 米横向偏差。`adas_calibration` 因此不返回单点漂亮解，而是：

- 联合优化内参、各视图外参、径向/切向畸变、路试俯仰与载荷形变；用数值秩命名当前采样几何下不可观测的参数；
- 名义解在中心证据下锚定为零畸变针孔模型，全部畸变自由度进入**可行集合包络**，给出中心/边缘路标像素误差与 40 米车道横向误差上界，并给出最有信息量的下次离轴靶位；
- 两档激活档案相互独立：`parking_assist` 只用画面中心与低速静态标线，经独立路标核验后发布受限参数且明确禁止转向控制；`lane_keeping` 要求全视场、两种以上载荷与 ≥30 km/h 动态俯仰采样下 40 米横向误差 ≤ 0.2 米，否则继续封锁；
- 换镜头或拆装支架令支架代次 +1，旧参数身份立即切断、历史保留并下发可执行撤回任务；新证据推翻已下发参数时同样保留激活历史并发撤回任务，不覆盖原版本；
- 维修工单的交车判定（`inspect` 依据 `road_test_ok` 置 `delivered`）不被标定结果改写。

CLI：`adas-calibrate --db … --camera … --at …`、`adas-release --profile parking_assist|lane_keeping`、`adas-mount-change`、`adas-withdraw`、`adas-withdrawal-execute`、`adas-calibrations`、`adas-activations`、`adas-withdrawal-tasks`、`adas-mount-state`。靶板可用 `ground_plane`、`verification`、`pitch_mrad` 描述地面平面、独立核验与安装俯仰。派生事件与原始证据一并入库，`audit` 从原件确定性重放全部标定、发布与撤回台账。
