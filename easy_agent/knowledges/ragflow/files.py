"""RAG 平台资源目录接入（RI001/RI002/RI010）：按目录上传与删除文件。"""

from __future__ import annotations

import json
from typing import Any, BinaryIO


class FileTreeMixin:
    """资源目录文件方法；由 RagflowClient 组合，复用其请求与校验基础设施。"""

    async def upload_files_by_tree(
        self,
        *,
        files: list[tuple[str, bytes | BinaryIO, str]],
        tree_code: str,
        metadata: dict[str, Any] | str | None = None,
        datasets: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按资源目录上传文档（RI001：POST /api/v1/abac/files）。

        按全行统一目录编码把知识文档挂到对应资源目录，一次请求可传多个
        文件，响应 data 数组返回每个文件状态信息。

        Args:
            files: (文件名, 文件内容, MIME 类型) 元组列表；``files`` 字段
                多文件时重复提交。
            tree_code: 知识目录编码（必填）。
            metadata: key/value 自定义元数据（JSON 字符串），dict 时序列化；
                RAG 检索可通过 metadata 过滤文档提升准确率。
            datasets: 目标知识库 id 列表；未提供则按目录与知识库映射关系
                批量调度加工。

        Returns:
            每个文件的上传结果列表（name/status/error/file 字段，file 内含
            id/tree_id/mime_type/size_bytes/metadata 等）。
        """
        if not files:
            raise ValueError("files 不能为空")
        self._require_non_empty(tree_code, "tree_code")
        form: dict[str, Any] = {"tree_code": tree_code}
        if metadata is not None:
            form["metadata"] = (
                json.dumps(metadata, ensure_ascii=False)
                if isinstance(metadata, dict)
                else metadata
            )
        if datasets:
            form["datasets"] = datasets
        multipart = [
            ("files", (filename, content, content_type))
            for filename, content, content_type in files
        ]
        data = await self._request(
            "POST", "/api/v1/abac/files", files=multipart, form=form
        )
        return self._expect_list(data, "RAG 平台资源目录上传未返回结果数组")

    async def upload_cbcm_files_by_tree(
        self,
        *,
        tree_code: str,
        service_id: str,
        batch_id: str,
        file_part: str,
        object_name: str,
        file_list: list[str] | None = None,
        metadata: dict[str, Any] | str | None = None,
        datasets: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按资源目录上传内容管理平台（CBCM）文档（RI002：POST /api/v1/abac/files/cbcm）。

        从 CBCM 内容管理平台按批次拉取文件挂到 tree_code 对应资源目录，
        不传本地文件。响应字段与 RI001 相同。

        Args:
            tree_code: 知识目录编码（必填）。
            service_id: 服务 ID（必填）。
            batch_id: 批次 ID（必填）。
            file_part: 文件部分标识（必填）。
            object_name: 对象名称（必填）。
            file_list: 文件列表编号；不传则批次下全部文件。
            metadata: key/value 自定义元数据（JSON 字符串），dict 时序列化。
            datasets: 实时解析加工到目标知识库 id 列表；未提供则按目录与
                知识库映射关系批量调度加工。
        """
        self._require_all(
            (tree_code, service_id, batch_id, file_part, object_name),
            "tree_code/service_id/batch_id/file_part/object_name 均为必填",
        )
        body = self._clean_body(
            {
                "tree_code": tree_code,
                "serviceId": service_id,
                "batchId": batch_id,
                "filePart": file_part,
                "objectName": object_name,
                "FileList": file_list,
                "metadata": (
                    metadata
                    if metadata is None or isinstance(metadata, str)
                    else json.dumps(metadata, ensure_ascii=False)
                ),
                "datasets": datasets,
            }
        )
        data = await self._request("POST", "/api/v1/abac/files/cbcm", json_body=body)
        return self._expect_list(data, "RAG 平台 CBCM 上传未返回结果数组")

    async def delete_file_by_tree(self, file_id: str) -> None:
        """按资源目录删除文档（RI010：DELETE /api/v1/abac/files/{id}）。

        按文件 id 删除资源目录下的文档；若文件已导入知识库，平台先按
        记录调用 Ragflow 删除对应文档再删本地文件。删除后不可恢复。
        """
        self._require_non_empty(file_id, "file_id")
        path = self._format_path("/api/v1/abac/files/{file_id}", file_id=file_id)
        await self._request("DELETE", path)
