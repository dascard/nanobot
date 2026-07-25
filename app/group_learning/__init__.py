"""群学习应用层。

包初始化不得导入具体 Application Service；调用方必须从明确子模块导入，
避免 Query、Schedule、Analysis 和 Scheduler 之间形成隐式生命周期环。
"""
