import logging
from datetime import datetime

from . import bloom_enum as en
from .bloom_repository import insert_bloom_data

logger = logging.getLogger("easy_agent.bloom")


def classification(content: str, bloom_date: str, db):
    data_line = get_data(content)
    process_data(data_line, bloom_date, db)


def classification_gz(content: str, bloom_date: str, db):
    data_line = get_data(content)
    process_data_gz(data_line, bloom_date, db)


def get_data(content: str):
    data_line = []
    data_section = False

    if content:
        for line in content.splitlines():
            line = line.strip()
            if line == "START-OF-DATA":
                data_section = True
                continue
            elif line == "END-OF-DATA":
                data_section = False
                logger.info("文件解析完成，共%d行数据", len(data_line))
                break
            if data_section:
                data_line.append(line)
    if data_section:
        logger.info("文件没有END-OF-DATA标识")
    return data_line


def process_data(data_line: list[str], bloom_date: str, db):
    data_dict = {}
    for line in data_line:
        try:
            fields = line.split('|')
            mem = en.BloomEnum.get_element(fields[0])
            if mem:
                data_dict[mem.value[0]] = fields
        except Exception as e:
            logger.error("彭博数据%s数据处理失败 %s", fields[0] if 'fields' in dir() else 'unknown', e)

    for fields in data_dict.values():
        member = en.BloomEnum.get_element(fields[0])
        try:
            bloom_code = member.value[0]
            bloom_code_cn = member.value[1]
            last_update = convert_date(fields[4])
            last_update_eod = convert_date(fields[6])
            type_ = member.value[2]
            ts = convert_date_to_big_int(bloom_date)

            if fields[3] == 'N.A.' and fields[5] == 'N.A.':
                continue
            if fields[3] == 'N.A.' and fields[5]:
                fields[3] = fields[5]
            elif fields[5] == 'N.A.' and fields[3]:
                fields[5] = fields[3]

            insert_bloom_data(
                db=db,
                bloom_code=bloom_code,
                bloom_code_cn=bloom_code_cn,
                px_last=float(fields[3]),
                last_update=last_update,
                px_last_eod=float(fields[5]),
                last_update_eod=last_update_eod,
                type_=type_,
                region=member.value[3],
                bloom_date=bloom_date,
                sbm_time=ts,
            )
        except Exception as e:
            logger.error("彭博数据%s数据处理失败 %s", fields[0], e)


def process_data_gz(data_line: list[str], bloom_date: str, db):
    data_dict = {}
    for line in data_line:
        try:
            fields = line.split('|')
            mem = en.BloomEnum.get_element(fields[0])
            if mem:
                data_dict[mem.value[0]] = fields
        except Exception as e:
            logger.error("彭博数据%s数据处理失败 %s", fields[0] if 'fields' in dir() else 'unknown', e)

    for fields in data_dict.values():
        member = en.BloomEnum.get_element(fields[0])
        try:
            bloom_code = member.value[0]
            bloom_code_cn = member.value[1]
            last_update = convert_date_gz(fields[3])
            last_update_eod = convert_date_gz(fields[3])
            type_ = member.value[2]
            ts = convert_date_to_big_int(bloom_date)

            if fields[4] == 'N.A.':
                continue

            insert_bloom_data(
                db=db,
                bloom_code=bloom_code,
                bloom_code_cn=bloom_code_cn,
                px_last=float(fields[4]),
                last_update=last_update,
                px_last_eod=float(fields[4]),
                last_update_eod=last_update_eod,
                type_=type_,
                region=member.value[3],
                bloom_date=bloom_date,
                sbm_time=ts,
            )
        except Exception as e:
            logger.error("彭博数据%s数据处理失败 %s", fields[0], e)


def convert_date(date_str):
    try:
        date_obj = datetime.strptime(date_str, "%m/%d/%Y")
        return date_obj.strftime("%Y-%m-%d")
    except Exception as e:
        logger.info("日期转换格式无效%s,message:%s", date_str, e)
        return None


def convert_date_gz(date_str):
    try:
        date_obj = datetime.strptime(date_str, "%Y%m%d")
        return date_obj.strftime("%Y-%m-%d")
    except Exception as e:
        logger.info("日期转换格式无效%s,message:%s", date_str, e)
        return None


def convert_date_to_big_int(data_str: str):
    try:
        date_obj = datetime.strptime(data_str, "%Y-%m-%d")
        ts = int(date_obj.timestamp() * 1000)
        return ts
    except Exception as e:
        logger.info("日期转换格式无效%s,message:%s", data_str, e)
        return None
