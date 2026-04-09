// MiniMaxAIDocx script: 生成智能体介绍文档
#r "nuget: DocumentFormat.OpenXml, 3.2.0"

using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Wordprocessing;

string outputPath = args.Length > 0 ? args[0] : "智能体介绍.docx";

using var doc = WordprocessingDocument.Create(outputPath, WordprocessingDocumentType.Document);
var mainPart = doc.AddMainDocumentPart();

// ============ 创建样式 ============
var stylesPart = mainPart.AddNewPart<StyleDefinitionsPart>();
stylesPart.Styles = new Styles();
SetupStyles(stylesPart);
stylesPart.Styles.Save();

// ============ 文档主体 ============
mainPart.Document = new Document(
    new Body(
        // 标题
        CreateTitle("智能体（AI Agent）技术介绍"),
        
        // 目录占位符
        CreateParagraph("目录", "TOCHeading"),
        CreateParagraph("[目录 - 打开文档后更新]"),
        
        CreateHeading1("一、智能体的定义"),
        CreateParagraph("智能体（AI Agent）是一种能够感知环境、做出决策并执行动作的人工智能系统。它具备自主性、反应性、主动性和社交能力，能够在没有人类直接干预的情况下完成复杂任务。"),
        
        CreateHeading2("1.1 智能体的核心特征"),
        CreateBulletPoint("自主性（Autonomy）：能够独立完成任务，无需人类持续干预"),
        CreateBulletPoint("感知能力（Perception）：能够理解和处理来自环境的信息"),
        CreateBulletPoint("推理能力（Reasoning）：能够基于知识和逻辑进行决策"),
        CreateBulletPoint("行动能力（Action）：能够执行具体动作并影响环境"),
        CreateBulletPoint("学习能力（Learning）：能够从经验中学习和改进"),
        
        CreateHeading2("1.2 智能体与传统程序的区别"),
        CreateTable(new[] { "特性", "传统程序", "智能体" },
                   new[] { "执行方式", "预设规则", "动态决策" },
                   new[] { "交互方式", "被动响应", "主动探索" },
                   new[] { "适应性", "固定行为", "持续学习" },
                   new[] { "自主程度", "低", "高" }),
        
        CreateHeading1("二、智能体的发展历史"),
        CreateParagraph("智能体技术的发展经历了多个重要阶段，从早期的人工智能研究到如今的大模型驱动时代。"),
        
        CreateHeading2("2.1 萌芽期（1950-1980年代）"),
        CreateBulletPoint("1956年：达特茅斯会议标志着人工智能作为独立学科的诞生"),
        CreateBulletPoint("1960年代：ELIZA等早期聊天程序出现，展示了人机对话的可能性"),
        CreateBulletPoint("1970年代：专家系统开始流行，为智能体知识表示奠定基础"),
        
        CreateHeading2("2.2 发展期（1980-2000年代）"),
        CreateBulletPoint("1986年： Brooks提出包容式体系结构（Subsumption Architecture）"),
        CreateBulletPoint("1990年代：多智能体系统（Multi-Agent System）研究兴起"),
        CreateBulletPoint("1997年：IBM深蓝击败国际象棋冠军，展示AI在特定领域的强大能力"),
        CreateBulletPoint("2000年代：统计学习和机器学习方法的突破"),
        
        CreateHeading2("2.3 成熟期（2010-2020年代）"),
        CreateBulletPoint("2012年：深度学习突破，AlexNet在ImageNet竞赛中取得重大进展"),
        CreateBulletPoint("2016年：AlphaGo击败围棋世界冠军，展示强化学习的潜力"),
        CreateBulletPoint("2017年：Transformer架构提出，为大语言模型奠定基础"),
        CreateBulletPoint("2020年：GPT-3发布，大模型时代正式开启"),
        CreateBulletPoint("2022年：ChatGPT发布，生成式AI进入公众视野"),
        CreateBulletPoint("2023年至今：GPT-4、Claude、Gemini等大模型持续进化，Agent框架快速发展"),
        
        CreateHeading2("2.4 智能体时代（2024年至今）"),
        CreateBulletPoint("多模态智能体：能够处理文本、图像、音频、视频等多种模态"),
        CreateBulletPoint("工具使用智能体：能够调用外部工具和API"),
        CreateBulletPoint("自主规划智能体：具备复杂任务规划和分解能力"),
        CreateBulletPoint("多智能体协作：多个智能体协同工作解决复杂问题"),
        
        CreateHeading1("三、智能体的使用场景"),
        
        CreateHeading2("3.1 个人助手场景"),
        CreateBulletPoint("智能客服：7×24小时在线解答用户问题，提供个性化服务"),
        CreateBulletPoint("个人秘书：协助处理日程、邮件、文档等工作任务"),
        CreateBulletPoint("学习助手：根据用户需求提供个性化的学习资源和辅导"),
        CreateBulletPoint("健康管理：监测健康数据，提供健康建议和提醒"),
        
        CreateHeading2("3.2 企业应用场景"),
        CreateBulletPoint("智能客服中心：处理大量客户咨询，降低人工成本"),
        CreateBulletPoint("数据分析助手：自动分析业务数据，生成洞察报告"),
        CreateBulletPoint("代码开发助手：辅助程序员编写、调试和优化代码"),
        CreateBulletPoint("文档处理：自动生成、编辑和总结各类文档"),
        CreateBulletPoint("流程自动化：自动化处理复杂的业务流程"),
        
        CreateHeading2("3.3 行业垂直场景"),
        CreateTable(new[] { "行业", "应用场景", "典型案例" },
                   new[] { "金融", "智能投顾、风险评估、反欺诈", "智能理财顾问" },
                   new[] { "医疗", "辅助诊断、健康管理、药物研发", "AI辅助诊疗系统" },
                   new[] { "教育", "个性化学习、智能评测、教育机器人", "自适应学习平台" },
                   new[] { "制造", "质量检测、预测性维护、智能调度", "智能工厂" },
                   new[] { "零售", "智能推荐、库存管理、客户服务", "个性化推荐系统" },
                   new[] { "法律", "合同审查、法律咨询、案例分析", "智能法律助手" }),
        
        CreateHeading2("3.4 新兴应用场景"),
        CreateBulletPoint("游戏智能体：作为游戏中的NPC或队友，提供更真实的交互体验"),
        CreateBulletPoint("具身智能：将智能体与机器人结合，实现物理世界的智能操作"),
        CreateBulletPoint("科学研究：辅助科学实验、数据分析和假说生成"),
        CreateBulletPoint("内容创作：辅助写作、绘画、音乐创作等艺术创作"),
        
        CreateHeading1("四、智能体的开发流程"),
        
        CreateHeading2("4.1 需求分析与规划阶段"),
        CreateBulletPoint("明确智能体的应用场景和目标"),
        CreateBulletPoint("定义智能体的功能范围和能力边界"),
        CreateBulletPoint("确定性能指标和评估标准"),
        CreateBulletPoint("分析数据需求和技术资源"),
        
        CreateHeading2("4.2 架构设计与技术选型"),
        CreateBulletPoint("确定智能体架构类型：单智能体、多智能体、层次化等"),
        CreateBulletPoint("选择基础模型：GPT-4、Claude、Llama、本地模型等"),
        CreateBulletPoint("设计记忆系统：短期记忆、长期记忆、向量数据库"),
        CreateBulletPoint("规划工具和插件体系"),
        CreateBulletPoint("设计人机交互界面"),
        
        CreateHeading2("4.3 核心模块开发"),
        CreateBulletPoint("规划器（Planner）：任务分解和执行计划生成"),
        CreateBulletPoint("记忆模块（Memory）：知识存储和检索"),
        CreateBulletPoint("工具调用（Tool Use）：外部API和插件集成"),
        CreateBulletPoint("安全控制（Safety）：输入输出过滤和权限管理"),
        CreateBulletPoint("评估机制（Evaluation）：结果验证和质量控制"),
        
        CreateHeading2("4.4 训练与优化"),
        CreateBulletPoint("Prompt工程：优化提示词以提升效果"),
        CreateBulletPoint("Fine-tuning：针对特定任务进行模型微调"),
        CreateBulletPoint("RLHF：人类反馈强化学习优化对齐"),
        CreateBulletPoint("性能调优：响应速度、资源消耗优化"),
        
        CreateHeading2("4.5 测试与部署"),
        CreateBulletPoint("单元测试：各模块功能验证"),
        CreateBulletPoint("集成测试：整体流程验证"),
        CreateBulletPoint("安全测试：对抗攻击、敏感信息过滤"),
        CreateBulletPoint("性能测试：并发、延迟、稳定性"),
        CreateBulletPoint("灰度发布与监控"),
        
        CreateHeading1("五、智能体的工作原理"),
        
        CreateHeading2("5.1 基础架构"),
        CreateParagraph("智能体的核心架构通常包含以下几个关键组件："),
        CreateBulletPoint("大脑（Brain）：基于大语言模型的推理引擎，负责理解、规划和决策"),
        CreateBulletPoint("感知（Perception）：处理和理解用户输入和外部环境信息"),
        CreateBulletPoint("记忆（Memory）：存储和管理知识、经验和上下文信息"),
        CreateBulletPoint("工具（Tools）：调用外部API和插件以扩展能力"),
        CreateBulletPoint("行动（Action）：执行具体操作并与外部世界交互"),
        
        CreateHeading2("5.2 核心工作流程"),
        CreateParagraph("智能体的工作流程通常遵循以下步骤："),
        CreateParagraph("1. 接收输入：智能体接收用户的指令或感知环境变化"),
        CreateParagraph("2. 理解意图：大语言模型理解用户的真实意图和需求"),
        CreateParagraph("3. 规划分解：将复杂任务分解为可执行的子任务"),
        CreateParagraph("4. 知识检索：从记忆中检索相关的知识和上下文"),
        CreateParagraph("5. 执行行动：按计划执行子任务，可能需要调用工具"),
        CreateParagraph("6. 评估结果：评估行动结果，决定是否需要调整计划"),
        CreateParagraph("7. 反馈学习：将执行结果存入记忆，供后续使用"),
        
        CreateHeading2("5.3 关键技术原理"),
        
        CreateHeading3("大语言模型（LLM）"),
        CreateParagraph("大语言模型是智能体的"大脑"，通常基于Transformer架构。它通过预训练学习海量的语言知识，具备强大的语言理解和生成能力。主流模型包括GPT-4、Claude、Llama、通义千问、文心一言等。"),
        
        CreateHeading3("提示工程（Prompt Engineering）"),
        CreateParagraph("通过精心设计的提示词引导模型产生期望的输出。关键技术包括：Few-shot示例、思维链提示（Chain of Thought）、角色扮演等。"),
        
        CreateHeading3("思维链（Chain of Thought）"),
        CreateParagraph("通过让模型展示推理过程来提升复杂任务的处理能力。研究表明，将问题分解为多个步骤逐步思考，可以显著提高答案的准确性。"),
        
        CreateHeading3("工具调用（Tool Use）"),
        CreateParagraph("智能体通过预定义的工具接口与外部世界交互。常见工具包括：搜索引擎、数据库查询、API调用、代码执行器、文件操作等。"),
        
        CreateHeading3("记忆系统（Memory）"),
        CreateParagraph("智能体的记忆系统通常包含三个层次："),
        CreateBulletPoint("感官记忆：处理当前的输入信息"),
        CreateBulletPoint("工作记忆：维护当前对话的上下文"),
        CreateBulletPoint("长期记忆：存储历史知识和经验，通常使用向量数据库实现"),
        
        CreateHeading2("5.4 主流框架介绍"),
        CreateTable(new[] { "框架", "开发商", "特点" },
                   new[] { "LangChain", "LangChain", "功能全面，社区活跃" },
                   new[] { "AutoGPT", "Significant Gravitas", "自主性强，任务分解" },
                   new[] { "GPT-Assistant", "OpenAI", "官方支持，易用性好" },
                   new[] { "CrewAI", "CrewAI", "多智能体协作，专为团队设计" },
                   new[] { "AutoGen", "Microsoft", "多智能体对话，代码生成" },
                   new[] { "Dify", "Dify.AI", "开源易用，支持可视化编排" }),
        
        CreateHeading1("六、未来发展趋势"),
        
        CreateHeading2("6.1 技术发展方向"),
        CreateBulletPoint("更强的推理能力：更复杂任务的自主解决"),
        CreateBulletPoint("多模态融合：文本、图像、视频、音频的统一理解与生成"),
        CreateBulletPoint("持续学习：在线学习和知识更新能力"),
        CreateBulletPoint("更好的可解释性：决策过程透明化"),
        CreateBulletPoint("更高的安全性：防止有害输出和滥用"),
        
        CreateHeading2("6.2 应用发展趋势"),
        CreateBulletPoint("深度垂直化：针对特定行业的专业化智能体"),
        CreateBulletPoint("端侧部署：智能体在手机、汽车等终端设备上运行"),
        CreateBulletPoint("个性化定制：满足个人用户独特需求的定制智能体"),
        CreateBulletPoint("人形机器人：智能体与机器人硬件的深度结合"),
        
        CreateHeading2("6.3 社会影响"),
        CreateBulletPoint("人机协作：人类与智能体协同工作的新模式"),
        CreateBulletPoint("产业变革：重塑各行各业的生产方式"),
        CreateBulletPoint("就业结构：部分岗位被替代，同时创造新的就业机会"),
        CreateBulletPoint("教育变革：个性化学习和终身学习的新机遇"),
        CreateBulletPoint("治理挑战：隐私保护、责任认定、伦理规范等新问题"),
        
        CreateHeading1("七、总结"),
        CreateParagraph("智能体作为人工智能发展的重要方向，正在从理论研究走向实际应用。随着大语言模型技术的快速发展和基础设施的不断完善，智能体将在越来越多的场景中发挥作用，成为人类生活和工作中不可或缺的助手。"),
        CreateParagraph("理解和掌握智能体技术，对于把握人工智能发展趋势、抓住时代机遇具有重要意义。无论是个人用户还是企业组织，都应该积极拥抱这一技术变革，在智能体时代占据先机。"),
        
        // 结尾部分
        CreateParagraph(""),
        CreateParagraph("文档生成时间：" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss")),
        CreateParagraph("Powered by MiniMaxAIDocx"),
        
        // 页面设置
        new SectionProperties(
            new PageSize { Width = (UInt32Value)11906U, Height = (UInt32Value)16838U },  // A4
            new PageMargin
            {
                Top = 1440,
                Right = (UInt32Value)1440U,
                Bottom = 1440,
                Left = (UInt32Value)1440U,
                Header = (UInt32Value)720U,
                Footer = (UInt32Value)720U
            }
        )
    )
);

mainPart.Document.Save();

Console.WriteLine($"文档已生成：{outputPath}");

// ============ 辅助方法 ============

void SetupStyles(StyleDefinitionsPart part)
{
    var styles = part.Styles!;
    
    // 文档默认样式
    var docDefaults = new DocDefaults();
    var rPrDefault = new RunPropertiesDefault(
        new RunPropertiesBaseStyle(
            new RunFonts { Ascii = "微软雅黑", HighAnsi = "微软雅黑", EastAsia = "微软雅黑", ComplexScript = "Arial" },
            new FontSize { Val = "24" },  // 12pt
            new FontSizeComplexScript { Val = "24" }
        )
    );
    var pPrDefault = new ParagraphPropertiesDefault(
        new ParagraphPropertiesBaseStyle(
            new SpacingBetweenLines { After = "200", Line = "276", LineRule = LineSpacingRuleValues.Auto }
        )
    );
    docDefaults.Append(rPrDefault);
    docDefaults.Append(pPrDefault);
    styles.Append(docDefaults);
    
    // 普通样式
    styles.Append(new Style { Type = StyleValues.Paragraph, StyleId = "Normal", StyleName = new StyleName { Val = "正文" } });
    
    // 标题1样式
    styles.Append(new Style
    {
        Type = StyleValues.Paragraph,
        StyleId = "Heading1",
        StyleName = new StyleName { Val = "标题 1" },
        PrimaryStyle = new PrimaryStyle(),
        StyleParagraphProperties = new StyleParagraphProperties(
            new KeepNext(),
            new SpacingBetweenLines { Before = "240", After = "120" },
            new OutlineLevel { Val = 0 }
        ),
        StyleRunProperties = new StyleRunProperties(
            new Bold(),
            new BoldComplexScript(),
            new FontSize { Val = "36" },  // 18pt
            new FontSizeComplexScript { Val = "36" },
            new Color { Val = "2F5496" }
        )
    });
    
    // 标题2样式
    styles.Append(new Style
    {
        Type = StyleValues.Paragraph,
        StyleId = "Heading2",
        StyleName = new StyleName { Val = "标题 2" },
        PrimaryStyle = new PrimaryStyle(),
        StyleParagraphProperties = new StyleParagraphProperties(
            new KeepNext(),
            new SpacingBetweenLines { Before = "200", After = "100" },
            new OutlineLevel { Val = 1 }
        ),
        StyleRunProperties = new StyleRunProperties(
            new Bold(),
            new BoldComplexScript(),
            new FontSize { Val = "28" },  // 14pt
            new FontSizeComplexScript { Val = "28" },
            new Color { Val = "2F5496" }
        )
    });
    
    // 标题3样式
    styles.Append(new Style
    {
        Type = StyleValues.Paragraph,
        StyleId = "Heading3",
        StyleName = new StyleName { Val = "标题 3" },
        PrimaryStyle = new PrimaryStyle(),
        StyleParagraphProperties = new StyleParagraphProperties(
            new KeepNext(),
            new SpacingBetweenLines { Before = "160", After = "80" },
            new OutlineLevel { Val = 2 }
        ),
        StyleRunProperties = new StyleRunProperties(
            new Bold(),
            new BoldComplexScript(),
            new FontSize { Val = "24" },  // 12pt
            new FontSizeComplexScript { Val = "24" },
            new Color { Val = "2F5496" }
        )
    });
    
    // 目录标题样式
    styles.Append(new Style
    {
        Type = StyleValues.Paragraph,
        StyleId = "TOCHeading",
        StyleName = new StyleName { Val = "目录标题" },
        PrimaryStyle = new PrimaryStyle(),
        StyleParagraphProperties = new StyleParagraphProperties(
            new SpacingBetweenLines { Before = "240", After = "120" },
            new Justification { Val = JustificationValues.Center }
        ),
        StyleRunProperties = new StyleRunProperties(
            new Bold(),
            new FontSize { Val = "32" },  // 16pt
            new FontSizeComplexScript { Val = "32" }
        )
    });
    
    // 列表样式
    styles.Append(new Style
    {
        Type = StyleValues.Paragraph,
        StyleId = "ListParagraph",
        StyleName = new StyleName { Val = "列表段落" },
        PrimaryStyle = new PrimaryStyle(),
        StyleParagraphProperties = new StyleParagraphProperties(
            new SpacingBetweenLines { After = "100" },
            new Indentation { Left = "720", Hanging = "360" }
        )
    });
    
    // 表格样式
    styles.Append(new Style
    {
        Type = StyleValues.Table,
        StyleId = "TableGrid",
        StyleName = new StyleName { Val = "网格型" },
        StyleParagraphProperties = new StyleParagraphProperties(),
        StyleTableProperties = new StyleTableProperties(
            new TableBorders(
                new TopBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "auto" },
                new LeftBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "auto" },
                new BottomBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "auto" },
                new RightBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "auto" },
                new InsideHorizontalBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "auto" },
                new InsideVerticalBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "auto" }
            )
        )
    });
}

Paragraph CreateParagraph(string text, string? styleId = null)
{
    var para = new Paragraph();
    if (styleId != null)
    {
        para.Append(new ParagraphProperties(new ParagraphStyleId { Val = styleId }));
    }
    para.Append(new Run(new Text(text) { Space = SpaceProcessingModeValues.Preserve }));
    return para;
}

Paragraph CreateTitle(string text)
{
    var para = new Paragraph();
    para.Append(new ParagraphProperties(
        new Justification { Val = JustificationValues.Center },
        new SpacingBetweenLines { Before = "480", After = "480" }
    ));
    para.Append(new Run(
        new RunProperties(
            new Bold(),
            new FontSize { Val = "56" },  // 28pt
            new FontSizeComplexScript { Val = "56" },
            new Color { Val = "2F5496" }
        ),
        new Text(text) { Space = SpaceProcessingModeValues.Preserve }
    ));
    return para;
}

Paragraph CreateHeading1(string text)
{
    var para = new Paragraph();
    para.Append(new ParagraphProperties(new ParagraphStyleId { Val = "Heading1" }));
    para.Append(new Run(new Text(text)));
    return para;
}

Paragraph CreateHeading2(string text)
{
    var para = new Paragraph();
    para.Append(new ParagraphProperties(new ParagraphStyleId { Val = "Heading2" }));
    para.Append(new Run(new Text(text)));
    return para;
}

Paragraph CreateHeading3(string text)
{
    var para = new Paragraph();
    para.Append(new ParagraphProperties(new ParagraphStyleId { Val = "Heading3" }));
    para.Append(new Run(new Text(text)));
    return para;
}

Paragraph CreateBulletPoint(string text)
{
    var para = new Paragraph();
    para.Append(new ParagraphProperties(
        new ParagraphStyleId { Val = "ListParagraph" },
        new NumberingProperties(
            new NumberingLevelReference { Val = 0 },
            new NumberingId { Val = 1 }
        )
    ));
    para.Append(new Run(new Text(text) { Space = SpaceProcessingModeValues.Preserve }));
    return para;
}

Table CreateTable(string[] headers, params string[][] rows)
{
    var table = new Table();
    
    // 表格属性
    table.Append(new TableProperties(
        new TableWidth { Width = "5000", Type = TableWidthUnitValues.Pct },
        new TableBorders(
            new TopBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "4472C4" },
            new LeftBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "4472C4" },
            new BottomBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "4472C4" },
            new RightBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "4472C4" },
            new InsideHorizontalBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "4472C4" },
            new InsideVerticalBorder { Val = BorderValues.Single, Size = (UInt32Value)4U, Color = "4472C4" }
        ),
        new TableCellMarginDefault(
            new TopMargin { Width = "80", Type = TableWidthUnitValues.Dxa },
            new TableCellLeftMargin { Width = 0, Type = TableWidthValues.Dxa },
            new BottomMargin { Width = "80", Type = TableWidthUnitValues.Dxa },
            new TableCellRightMargin { Width = 0, Type = TableWidthValues.Dxa }
        )
    ));
    
    // 表头
    var headerRow = new TableRow();
    foreach (var header in headers)
    {
        var cell = new TableCell();
        cell.Append(new TableCellProperties(
            new Shading { Val = ShadingPatternValues.Clear, Fill = "4472C4" },
            new TableCellVerticalAlignment { Val = TableVerticalAlignmentValues.Center }
        ));
        cell.Append(new Paragraph(
            new ParagraphProperties(new Justification { Val = JustificationValues.Center }),
            new Run(
                new RunProperties(new Bold(), new Color { Val = "FFFFFF" }),
                new Text(header)
            )
        ));
        headerRow.Append(cell);
    }
    table.Append(headerRow);
    
    // 数据行
    bool isAlternate = false;
    foreach (var row in rows)
    {
        var tableRow = new TableRow();
        foreach (var cellText in row)
        {
            var cell = new TableCell();
            string fillColor = isAlternate ? "D6DCE4" : "FFFFFF";
            cell.Append(new TableCellProperties(
                new Shading { Val = ShadingPatternValues.Clear, Fill = fillColor },
                new TableCellVerticalAlignment { Val = TableVerticalAlignmentValues.Center }
            ));
            cell.Append(new Paragraph(
                new ParagraphProperties(new Justification { Val = JustificationValues.Left }),
                new Run(new Text(cellText) { Space = SpaceProcessingModeValues.Preserve })
            ));
            tableRow.Append(cell);
        }
        table.Append(tableRow);
        isAlternate = !isAlternate;
    }
    
    return table;
}
