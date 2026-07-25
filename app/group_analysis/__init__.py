"""群分析应用层。

包初始化保持无副作用；Tool、Scheduler 与 Web Adapter 从明确子模块引用共享
Application Service，不能依赖包导入顺序启动分析链。
"""
