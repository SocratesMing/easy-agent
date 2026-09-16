/*
 * Thrift2 最小 IDL —— 只声明 hbaseqa 用到的部分。
 *
 * WHY 需要这个文件：HBase 有两个互不兼容的 Thrift 服务。happybase 走的是
 * Thrift1 的 `Hbase` 服务（`getTableNames()` / `scannerOpenWithScan`），
 * 而 `hbase thrift2 start` 起的是 Thrift2 的 `THBaseService`，方法名完全不同，
 * 于是报 `TApplicationException: Invalid method name: 'getTableNames'`。
 * 本机只有 Thrift2，所以改成直接讲 Thrift2。
 *
 * 字段 ID 与类型逐字取自 Apache HBase 上游（Apache License 2.0）：
 *   hbase-thrift/src/main/resources/org/apache/hadoop/hbase/thrift2/hbase.thrift
 *
 * 只声明子集是安全的，两端的兼容规则保证了这点：
 * - 请求方向：服务端按**方法名**分发、按**字段 ID** 读参数，未发送的字段用默认值
 * - 响应方向：服务端多返回的字段由 thriftpy2 按 ID 跳过
 * 所以这里刻意省掉了 `readType` / `consistency` 这类枚举字段，避免枚举取值
 * 与服务端版本不一致时解码报错。
 *
 * 注意方法名与 Thrift1 的对应关系（不要混用）：
 *   getTableNamesByPattern  ←→ Thrift1 的 getTableNames
 *   openScanner             ←→ Thrift1 的 scannerOpenWithScan
 *   getScannerRows          ←→ Thrift1 的 scannerGetList
 *   closeScanner            ←→ Thrift1 的 scannerClose
 */
namespace py hbase_thrift2

struct TColumn {
  1: optional binary family,
  2: optional binary qualifier,
  3: optional i64 timestamp,
}

struct TColumnValue {
  1: optional binary family,
  2: optional binary qualifier,
  3: optional binary value,
  4: optional i64 timestamp,
}

struct TResult {
  1: optional binary row,
  2: optional list<TColumnValue> columnValues,
}

struct TTimeRange {
  1: optional i64 minStamp,
  2: optional i64 maxStamp,
}

struct TScan {
  1: optional binary startRow,
  2: optional binary stopRow,
  3: optional list<TColumn> columns,
  4: optional i32 caching,
  5: optional i32 maxVersions,
  7: optional binary filterString,
  8: optional i32 batchSize,
  /* 11: 反向扫描，Thrift2 原生支持（Thrift1 要靠 happybase 探测） */
  11: optional bool reversed,
  /* 15: 服务端行数上限，Thrift2 原生支持（Thrift1 只能在客户端截断） */
  15: optional i32 limit,
}

struct TTableName {
  1: optional binary ns,
  2: optional binary qualifier,
}

struct TColumnFamilyDescriptor {
  1: optional binary name,
}

struct TTableDescriptor {
  1: optional TTableName tableName,
  2: optional list<TColumnFamilyDescriptor> columns,
}

/* 写入用。注意字段 ID 从 3 直接跳到 5（上游就是缺 4，不是笔误）。
   本业务只在 CLI seed 脚本里用写方法，**不暴露为 MCP 工具**。 */
struct TPut {
  1: optional binary row,
  2: optional list<TColumnValue> columnValues,
  3: optional i64 timestamp,
}

exception TIOError {
  1: optional string message,
  2: optional bool canRetry,
}

exception TIllegalArgument {
  1: optional string message,
}

service THBaseService {
  list<TTableName> getTableNamesByPattern(
    1: string regex,
    2: bool includeSysTables
  ) throws (1: TIOError io)

  TTableDescriptor getTableDescriptor(
    1: TTableName table
  ) throws (1: TIOError io)

  list<TTableDescriptor> getTableDescriptors(
    1: list<TTableName> tables
  ) throws (1: TIOError io)

  i32 openScanner(
    1: binary table,
    2: TScan tscan
  ) throws (1: TIOError io)

  list<TResult> getScannerRows(
    1: i32 scannerId,
    2: i32 numRows
  ) throws (1: TIOError io, 2: TIllegalArgument ia)

  void closeScanner(
    1: i32 scannerId
  ) throws (1: TIOError io, 2: TIllegalArgument ia)

  /* 上游返回枚举 TThriftServerType；这里刻意声明成 i32：
     枚举按 i32 上线，声明成 i32 既兼容又能避免取值不认识时解码失败。
     0 = Thrift1(ONE)，1 = Thrift2(TWO) */
  i32 getThriftServerType()

  /* ── 以下为写方法，仅供 CLI seed 脚本（seed.py）使用 ──
     问数链路（server.py 的工具）永远不会调用它们。 */
  void put(
    1: binary table,
    2: TPut tput
  ) throws (1: TIOError io)

  void putMultiple(
    1: binary table,
    2: list<TPut> tputs
  ) throws (1: TIOError io)
}
