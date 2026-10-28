# 发布这个仓库

此文件夹是独立源码快照，不带原开发仓库的 Git 历史、运行数据或私有配置。首次发布只上传这个文件夹的内容。

1. 在 GitHub 创建名为 timing-studio 的 **Public** 空仓库，不自动创建 README、gitignore 或 License。
2. 在本文件夹根目录执行下面命令，将 YOUR-ACCOUNT 换成自己的 GitHub 用户名：

```powershell
git init -b main
git add .
git commit -m "Release Timing Studio skill 0.1.0"
git remote add origin https://github.com/YOUR-ACCOUNT/timing-studio.git
git push -u origin main
```

若 Git 尚未配置提交身份，按提示设置自己的姓名和邮箱。不要在源码、命令历史或聊天里填写访问令牌；通过 Git 的登录流程认证即可。

3. 发布后检查 README、LICENSE、skills/timing-studio/scripts/runtime 和示例都存在。GitHub 网页的 Code → Download ZIP 即可取得完整源码。
4. 对外安装说明提供仓库链接和 skill 子目录 `skills/timing-studio`。可另建 0.1.0 Release 附上源码 ZIP；不要附带运行时缓存、用户工程或浏览器资料。

后续改动先完成回归，更新 VERSION 和验证记录，用 `python refresh-manifests.py` 刷新已登记文件的哈希，再提交。若增加或删除公开文件，需要相应调整清单文件列表。
