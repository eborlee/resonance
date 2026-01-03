# Project Structure

Project name: tv2tg-core

This document records the directory structure and the responsibility of each part.
It is not a design document and does not describe business logic in detail.

---

## Root

根目录结构说明

tv2tg-core/
├── app/
│ 项目核心代码目录
│
├── docker/
│ Docker 及 docker-compose 相关文件
│
├── docs/
│ 项目文档目录（当前文件所在位置）
│
├── data/
│ 运行期数据目录（日志、临时状态，不纳入版本控制）
│
├── requirements.txt
│ Python 依赖列表
│
├── .env.example
│ 环境变量模板文件
│
├── .gitignore
│ Git 忽略规则
│
└── README.md
项目说明入口文档