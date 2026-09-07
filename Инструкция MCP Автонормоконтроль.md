# **Описание MCP-сервера**

Предоставляется MCP HTTP-сервер `Автонормоконтроль`, предназначенный для проверки PDF-документов на соответствие требованиям ГОСТ 7.32-2017 и ЕСПД.  
Сервер доступен по MCP HTTP endpoint: `http://mcp.10.32.11.60.nip.io`  
MCP сервер публикует:

* prompt `about_server` с общими инструкциями по использованию  
* tool `list_controls_and_dags` для получения списка доступных контролей  
* tool `submit_document` для отправки PDF на проверку  
* tool `generate_pdf_report` для отправки PDF на проверку

**Пример сценария для клиента**  
Клиент:

* вызывает `list_prompts()` и получает prompt `about_server`  
* вызывает `get_prompt("about_server")` и получает текст инструкции, как работать с инструментами сервиса  
* вызывает `list_tools()` и получает описание доступных инструментов  
* вызывает `submit_document` с PDF в base64, получает отчет и комментарии к pdf документу  
* вызывает `generate_pdf_report` с run\_id и dag\_id, для генерации отчета

# **Фрагмент кода для получения справочной работы по работе MCP сервера:**

| import asyncio from fastmcp import Client async def progress\_handler(progress: float, total: float | None, message: str | None) \-\> None:    pct \= f"{progress / total \* 100:.1f}%" if total else f"{progress}"    print(f"\[progress\] {pct} \- {message or ''}") async def get\_info(url: str \= 'http://mcp.10.32.11.60.nip.io') \-\> None:    client \= Client(url, progress\_handler\=progress\_handler)    async with client:        prompts\_response \= await client.list\_prompts()        print('Запросы')        for i, prompt in enumerate(prompts\_response):            print(i, (                f"{prompt.name} with title '{prompt.title}': {prompt.description}\\n"                f"arguments: {prompt.arguments}"            ))        prompt\_result \= await client.get\_prompt("about\_server")        print('\\nAbout: ', prompt\_result.messages\[0\].content.text)        print('\\nИнструменты')        tools\_response \= await client.list\_tools()        for i, tool in enumerate(tools\_response):            print(i, (                f"{tool.name} with title '{tool.title}'\\n"                f"{tool.description}\\n"                f"\\tinputSchema: {tool.inputSchema}\\n"                f"\\toutputSchema: {tool.outputSchema}\\n"                "----------------------------"            ))   if \_\_name\_\_ \== "\_\_main\_\_":    asyncio.run(get\_info()) |
| :---- |

Пример ответа

| ```Запросы 0 about_server with title 'None': Получить общие инструкции и описание сервера 'Автонормоконтроль'. arguments: [] About:  Ты — ассистент, использующий сервер 'Автонормоконтроль'. Этот сервер предназначен для проверки PDF-документов по ГОСТу 7.32-2017 и ЕСПД стандартам на соответствие нормативным требованиям. Инструкции по работе: 1. Используй инструмент `list_controls_and_dags`, если нужно показать доступные контроли. 2. Используй инструмент `submit_document` для отправки PDF на проверку. 3. Если проверка завершилась успешно, сообщай `task_id`, найденные ошибки, предупреждения и ссылки на output.pdf и report.pdf, если они есть. 4. Используй инструмент `generate_pdf_report`, чтобы получить report.pdf ранее запущенной проверки по `run_id`. 5. Если обработка завершилась ошибкой, явно сообщай, что пайплайн не смог завершить проверку. Всегда отвечай пользователю на русском языке, будь краток и опирайся только на реально доступные инструменты сервера. Инструменты: 0 submit_document with title 'None' Submit a PDF document for GOST compliance checking. Uploads the PDF and triggers the selected Airflow DAG. When ``dag_id`` is omitted, the server uses the default configured normcontrol DAG. 	inputSchema: {'additionalProperties': False, 'properties': {'pdf_file': {'format': 'base64', 'type': 'string'}, 'dag_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None}}, 'required': ['pdf_file'], 'type': 'object'} 	outputSchema: {'description': 'Result returned by the submit_document tool.', 'properties': {'task_id': {'description': 'Airflow DAG run ID', 'type': 'string'}, 'status': {'enum': ['queued', 'running', 'done', 'failed'], 'type': 'string', 'description': 'Current task status'}, 'output_pdf': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Presigned URL to download the corrected PDF (valid 24 hours)'}, 'report_pdf': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Presigned URL to download the PDF report (valid 24 hours)'}, 'errors': {'description': 'List of detected GOST 7.32 errors', 'items': {'type': 'string'}, 'type': 'array'}, 'warnings': {'description': 'List of warnings', 'items': {'type': 'string'}, 'type': 'array'}, 'message': {'description': 'Brief human-readable summary', 'type': 'string'}}, 'required': ['task_id', 'status', 'message'], 'type': 'object'} ---------------------------- 1 generate_pdf_report with title 'None' Вернуть PDF-отчёт нормоконтроля для указанного Airflow run_id. Если DAG ещё выполняется, инструмент дождётся завершения. Параметр ``dag_id`` нужен для запуска, созданного не в DAG по умолчанию. Повторный нормоконтроль не запускается. 	inputSchema: {'additionalProperties': False, 'properties': {'run_id': {'type': 'string'}, 'dag_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None}}, 'required': ['run_id'], 'type': 'object'} 	outputSchema: {'description': 'Result returned by the generate_pdf_report tool.', 'properties': {'run_id': {'description': 'Airflow DAG run ID', 'type': 'string'}, 'status': {'enum': ['queued', 'running', 'done', 'failed'], 'type': 'string', 'description': 'Current status of the DAG run'}, 'report_pdf': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Presigned URL to download the PDF report (valid 24 hours)'}, 'message': {'description': 'Brief human-readable summary', 'type': 'string'}}, 'required': ['run_id', 'status', 'message'], 'type': 'object'} ---------------------------- 2 list_controls_and_dags with title 'None' Return document controls and their linked Airflow DAG identifiers. Set ``include_templates`` to ``False`` to return only user-created controls. A control whose ``dag_id`` is null has not yet been deployed to Airflow. 	inputSchema: {'additionalProperties': False, 'properties': {'include_templates': {'default': True, 'type': 'boolean'}}, 'type': 'object'} 	outputSchema: {'properties': {'result': {'items': {'description': 'A control together with its optional Airflow DAG identifier.', 'properties': {'id': {'description': 'Database identifier of the document control', 'type': 'integer'}, 'code': {'description': 'Short control code', 'type': 'string'}, 'name': {'description': 'Human-readable control name', 'type': 'string'}, 'dag_id': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, 'description': 'Airflow DAG identifier linked to the control'}, 'is_template': {'description': 'Whether the control is a reusable template', 'type': 'boolean'}}, 'required': ['id', 'code', 'name', 'is_template'], 'type': 'object'}, 'type': 'array'}}, 'required': ['result'], 'type': 'object', 'x-fastmcp-wrap-result': True} ----------------------------```  |
| :---- |

# **Инструменты**

## **1\. Инструмент `submit_document`**

**Назначение:** Отправка PDF-документа на проверку.  
**Вход:**  
{  
  "pdf\_file": "\<base64-строка PDF\>"  
}  
**Формат входного параметра:**

* `pdf_file`: обязательное поле  
* тип: `string`  
* содержимое: PDF-файл в формате base64

**Выход:**  
{  
  "task\_id": "string",  
  "status": "queued | running | done | failed",  
  "output\_pdf": "string | null",  
  "report\_pdf": "string | null",  
  "errors": \["string", "..."\],  
  "warnings": \["string", "..."\],  
  "message": "string"  
}  
Пояснение по полям ответа:

* `task_id`: идентификатор запуска проверки  
* `status`: текущий статус обработки  
* `output_pdf`: ссылка на исправленный или результирующий PDF, если доступен  
* `report_pdf`: ссылка на сгенерированный отчет  
* `errors`: список найденных ошибок  
* `warnings`: список предупреждений  
* `message`: краткое текстовое описание результата

Программный код чтобы вызвать данный инструмент (документ для тестирования можно найти по [ссылке](https://disk.yandex.ru/i/i8xQv13kQ1WAng)):

| import asyncio import base64 import json from pathlib import Path import httpx from fastmcp import Client  async def progress\_handler(progress: float, total: float | None, message: str | None) \-\> None:    pct \= f"{progress / total \* 100:.1f}%" if total else f"{progress}"    print(f"\[progress\] {pct} \- {message or ''}") async def save\_pdf(http, data, key: str, path\_output: str) \-\> None:    url \= data.get(key)    if url is None:        print(f"{key} не сформирован")        return    res \= await http.get(url)    res.raise\_for\_status()    Path(path\_output).write\_bytes(res.content) async def send\_report(    url: str \= 'http://mcp.10.32.11.60.nip.io',    dag\_id: str \= 'flow-dqc-control-10',    pdf\_path: Path | str \= 'demo\_test.pdf' ) \-\> str:    if isinstance(pdf\_path, str):        pdf\_path \= Path(pdf\_path)    path\_output\_result \= Path('send\_result.pdf')    path\_output\_report \= Path('send\_report.pdf')    pdf\_bytes \= pdf\_path.read\_bytes()    pdf\_b64 \= base64.b64encode(pdf\_bytes).decode("ascii")    print(f"Submitting {pdf\_path.name} ({len(pdf\_bytes)} bytes)...")    client \= Client(url, progress\_handler\=progress\_handler)    async with client:        result \= await client.call\_tool(            "submit\_document",            {"pdf\_file": pdf\_b64, "dag\_id": dag\_id},        )        data \= result.structured\_content    print("\\n\--- Result \---")    print(json.dumps(result.structured\_content, ensure\_ascii\=False, indent\=2))    async with httpx.AsyncClient() as http:        await save\_pdf(http, data, 'output\_pdf', str(path\_output\_result))        await save\_pdf(http, data, 'report\_pdf', str(path\_output\_report))    return data\['task\_id'\]   if \_\_name\_\_ \== "\_\_main\_\_":    asyncio.run(send\_report()) |
| :---- |

После выполнения скрипта в папке со скриптом должны появиться “send\_result.pdf” и “send\_report.pdf” файлы. В консоле будет следующие логи:

| `Submitting demo_test.pdf (349978 bytes)... [progress] 0.0% - Uploading document [progress] 5.0% - Triggering normcontrol pipeline [progress] 10.0% - Processing started [progress] 10.0% - Queued (0/13) [progress] 10.0% - Queued (0/13) [progress] 16.0% - Running (1/13) [progress] 34.0% - Running (4/13) [progress] 40.0% - Running (5/13) [progress] 46.0% - Running (6/13) [progress] 53.0% - Running (7/13) [progress] 59.0% - Running (8/13) [progress] 59.0% - Running (8/13) [progress] 59.0% - Running (8/13) [progress] 59.0% - Running (8/13) [progress] 59.0% - Running (8/13) [progress] 59.0% - Running (8/13) [progress] 65.0% - Done (9/13) [progress] 90.0% - Downloading results [progress] 100.0% - Completed --- Result --- {   "task_id": "manual__2026-08-28T12:16:58.223955+00:00",   "status": "done",   "output_pdf": "http://localhost:9010/data/output/1f30788a39144b24896de852c5fcdf85/output.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=admin%2F20260828%2Fru-central1%2Fs3%2Faws4_request&X-Amz-Date=20260828T121811Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host&X-Amz-Signature=d2fa6b77746cdc356cf3885528de85f90125f73ab5292a685d4af6a2bba38d7f",   "report_pdf": "http://localhost:9010/data/output/1f30788a39144b24896de852c5fcdf85/report.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=admin%2F20260828%2Fru-central1%2Fs3%2Faws4_request&X-Amz-Date=20260828T121811Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host&X-Amz-Signature=fccddb45d7b28a092d5562b78e50d5ce5443e6ab3cdfb58d0e753a7015449e63",   "errors": [     "Ошибка на странице 6: Таблица 1.3: в конце наименования таблицы не должно быть знаков препинания",     "Ошибка на странице 6: Таблица 1.3: после номера таблицы должен стоять тире («Таблица 1 – Название»)",     "Ошибка на странице 6: Таблица 1.3: наименование таблицы после тире должно начинаться с заглавной буквы",     "Ошибка на странице 6: Таблица 1.3: отсутствует ссылка на таблицу в тексте",     "Ошибка на странице 6: Таблица 1.3: нарушен порядок нумерации таблиц (пропуск номера)",     "Ошибка на странице 7: Таблица: отсутствует наименование таблицы",     "Ошибка на странице 5: Заголовок «1.1 Рисунки»: заголовок должен быть выделен жирным",     "Ошибка на странице 6: Заголовок «1.2 Таблицы»: заголовок должен быть выделен жирным",     "Ошибка на странице 5: Рисунок 1.2: рисунок должен быть выровнен по центру",     "Ошибка на странице 5: Рисунок 1.2: в конце подписи рисунка не должно быть точки",     "Ошибка на странице 5: Рисунок 1.2: подпись рисунка должна быть отцентрована",     "Ошибка на странице 5: Рисунок 1.2: отсутствует ссылка на рисунок в тексте",     "Ошибка на странице 6: Рисунок: отсутствует подпись рисунка",     "Ошибка на странице 0: Титульный лист: в блоке «УТВЕРЖДАЮ» дублируется название вуза (оно уже указано в шапке титульного листа)",     "Ошибка на странице 0: Титульный лист: шапка «Министерство науки и высшего образования Российской Федерации» — неверный регистр",     "Ошибка на странице 0: Титульный лист: на титульном листе отсутствует сокращённое название «Университет ИТМО»",     "Ошибка на странице 0: Титульный лист: «УДК» не содержит номер, отделённый пробелом",     "Ошибка на странице 0: Титульный лист: недопустимые разделители «:» или «;» после «УДК»",     "Ошибка на странице 0: Титульный лист: в строке «город год» на титульном листе недопустима запятая",     "Ошибка на странице 0: Титульный лист: год должен быть на одной строке с городом"   ],   "warnings": [],   "message": "Processing completed successfully" }` |
| :---- |

## **2\. Инструмент `generate_pdf_report`**

**Назначение:** Генерация отчета по пройденному контролю.  
**Вход:**  
{  
  "run\_id": "\<str\>",  
  "dag\_id": "\<str\>"  
}  
**Формат входного параметра:**

* run\_id: string, обязательное поле (вернется при использовании инструмента “submit\_document”)  
* dag\_id: string, необязательное поле (по run\_id должен сам найти dag\_id, но пока стоит указать. Для нормоконтроля отчетов о НИР это “flow-dqc-control-10”)

**Выход:**  
{  
  "run\_id": "string",  
  "status": "queued | running | done | failed",  
  "report\_pdf": "string | null",  
  "message": "string"  
}  
Пояснение по полям ответа:

* `run_id`: идентификатор запуска проверки  
* `status`: текущий статус обработки  
* `report_pdf`: ссылка на сгенерированный отчет в формате pdf  
* `message`: краткое текстовое описание результата

Программный код чтобы вызвать данный инструмент (ради тестирования можно использовать run\_id “manual\_\_2026-08-28T17:43:45.975426+00:00” и dag\_id “flow-dqc-control-10”):

| import asyncio import base64 import json from pathlib import Path import httpx from fastmcp import Client  async def progress\_handler(progress: float, total: float | None, message: str | None) \-\> None:    pct \= f"{progress / total \* 100:.1f}%" if total else f"{progress}"    print(f"\[progress\] {pct} \- {message or ''}") async def save\_pdf(http, data, key: str, path\_output: str) \-\> None:    url \= data.get(key)    if url is None:        print(f"{key} не сформирован")        return    res \= await http.get(url)    res.raise\_for\_status()    Path(path\_output).write\_bytes(res.content) async def send\_report\_generation(    url: str \= 'http://mcp.10.32.11.60.nip.io',    dag\_id: str \= 'flow-dqc-control-10',    run\_id: str \= 'manual\_\_2026-08-28T17:43:45.975426+00:00' ) \-\> None:    path\_output\_report \= Path('send\_report\_2.pdf')    client \= Client(url, progress\_handler\=progress\_handler)    async with client:        result \= await client.call\_tool(            "generate\_pdf\_report",            {"run\_id": run\_id, "dag\_id": dag\_id},        )        data \= result.structured\_content    print("\\n\--- Result \---")    print(json.dumps(result.structured\_content, ensure\_ascii\=False, indent\=2))    async with httpx.AsyncClient() as http:        await save\_pdf(http, data, 'report\_pdf', str(path\_output\_report))   if \_\_name\_\_ \== "\_\_main\_\_":    asyncio.run(send\_report\_generation()) |
| :---- |

После выполнения скрипта в папке со скриптом должны появиться “send\_report\_2.pdf” файлы. В консоле будет следующие логи:

| `[progress] 10.0% - Processing started [progress] 65.0% - Done (9/13) [progress] 90.0% - Preparing PDF report [progress] 100.0% - Completed --- Result --- {   "run_id": "manual__2026-08-28T12:16:58.223955+00:00",   "status": "done",   "report_pdf": "http://localhost:9010/data/output/1f30788a39144b24896de852c5fcdf85/report.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=admin%2F20260828%2Fru-central1%2Fs3%2Faws4_request&X-Amz-Date=20260828T121813Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host&X-Amz-Signature=623261d4788f30ccbed46473d8e5cbb2200b57b23bd0e5e7e104bb55fb36713b",   "message": "PDF report is ready" }` |
| :---- |

## **3\. Инстурмент `list_controls_and_dags`**

**Назначение:** Получение списка доступных контролей (куда отправлять проверку, НИР или ТЗ из ЕСПД).  
**Выход:**  
Список объектов следующего вида:  
{  
  "id": 123,  
  "code": "string",  
  "name": "string",  
  "dag\_id": "string | null",  
  "is\_template": true  
}