import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Select, TextInput, Modal
import asyncio
import logging
import os
import threading
from flask import Flask
from datetime import datetime
from discord import app_commands

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')



# Discord Intents
intents = discord.Intents.default()
intents.guilds = True
intents.members = True          # メンバー情報取得に必要
intents.message_content = True  # コマンドやテキスト内容に必要

bot = commands.Bot(command_prefix="!", intents=intents)

# VCを作成するカテゴリID（あなたのサーバーに合わせて設定してください）
VC_CATEGORY_ID = 1369086223049687070 

# ロールID一覧（あなたのサーバーにあるランクロールIDを設定してください）
RANK_ROLE_IDS = [
    1358395001330991114,  # だれでもOK募集
    1375623172912840774,  # アイアン募集
    1375623375182893139,  # ブロンズ募集
    1375623542934208573,  # シルバー募集
    1375623724518211594,  # ゴールド募集
    1375623862959603833,  # プラチナ募集
    1375624068077715566,  # ダイヤモンド募集
    1375624192082579549,  # アセンダント募集
    1375624256079265902,  # イモータル募集
    1375624306415108157,  # レディアント募集
]

# 募集開始ボタンを設置するチャンネルID（例: #募集-ボタン）
RECRUIT_BUTTON_CHANNEL_ID = 1377062614231810199 

# 募集内容のEmbedを投稿するチャンネルID
RECRUIT_POST_CHANNEL_ID = 1380821926913769543

# グローバル変数
# キー: discord.Member.id (募集主のID)
# 値: RecruitFlow インスタンス
active_recruit_flows = {}

# 募集開始ボタンのメッセージを管理するための辞書
# キー: discord.TextChannel.id (募集開始ボタンが設置されるチャンネルID)
# 値: discord.Message.id (そのチャンネル内の募集開始ボタンメッセージID)
start_button_message_info = {}

# 募集開始ボタンのメッセージを定期的に確認・更新する間隔 (秒)
START_BUTTON_UPDATE_INTERVAL = 60 * 5 # 5分ごとに確認

class RecruitFlow:
    """募集フローの状態を保持するクラス"""
    def __init__(self):
        self.mode = None
        self.people_to_recruit = None
        self.total_party_size = None
        self.roles = []
        self.title = ""
        self.vc_channel = None
        self.message = None 
        self.participants = [] 
        self.vc_check_task = None 

class ModeSelect(discord.ui.Select):
    """ゲームモード選択用ドロップダウン"""
    def __init__(self, flow: RecruitFlow):
        self.flow = flow
        options = [
            discord.SelectOption(label="コンペティティブ", value="コンペ"),
            discord.SelectOption(label="アンレート", value="アンレート")
        ]
        super().__init__(placeholder="ゲームモードを選択", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id not in active_recruit_flows:
            await interaction.response.send_message("募集フローが見つかりませんでした。再度`!募集開始`コマンドを実行するか、募集開始ボタンを押してください。", ephemeral=True)
            return
        self.flow = active_recruit_flows[interaction.user.id]

        self.flow.mode = self.values[0]
        await interaction.response.edit_message(
            content="次に募集人数を選んでください：",
            view=PeopleSelectView(self.flow)
        )

class ModeSelectView(View):
    """ゲームモード選択を含むView"""
    def __init__(self, flow):
        super().__init__()
        self.flow = flow
        self.add_item(ModeSelect(flow))


class PeopleSelect(discord.ui.Select):
    """募集人数選択用ドロップダウン"""
    def __init__(self, flow: RecruitFlow):
        self.flow = flow
        options = [
            discord.SelectOption(label="デュオ（あと1人募集）", value="1"), 
            discord.SelectOption(label="トリオ（あと2人募集）", value="2"), 
            discord.SelectOption(label="フルパ（あと4人募集）", value="4")  
        ]
        super().__init__(placeholder="募集人数を選択", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id not in active_recruit_flows:
            await interaction.response.send_message("募集フローが見つかりませんでした。再度`!募集開始`コマンドを実行するか、募集開始ボタンを押してください。", ephemeral=True)
            return
        self.flow = active_recruit_flows[interaction.user.id]

        self.flow.people_to_recruit = int(self.values[0])
        self.flow.total_party_size = self.flow.people_to_recruit + 1 
        await interaction.response.edit_message(
            content="次に対象ランクを選んでください：",
            view=RankSelectView(self.flow, interaction.guild)
        )

class PeopleSelectView(View):
    """募集人数選択を含むView"""
    def __init__(self, flow):
        super().__init__()
        self.flow = flow
        self.add_item(PeopleSelect(flow))


class RankSelect(discord.ui.Select):
    """対象ランク選択用ドロップダウン"""
    def __init__(self, flow: RecruitFlow, guild: discord.Guild):
        self.flow = flow
        options = []
        for role_id in RANK_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                options.append(discord.SelectOption(label=role.name, value=str(role.id)))

        super().__init__(
            placeholder="対象ランクを選んでください（複数可）",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id not in active_recruit_flows:
            await interaction.response.send_message("募集フローが見つかりませんでした。再度`!募集開始`コマンドを実行するか、募集開始ボタンを押してください。", ephemeral=True)
            return
        self.flow = active_recruit_flows[interaction.user.id]

        self.flow.roles = self.values
        await interaction.response.edit_message(
            content="対象ランクを選択しました。募集タイトルを入力してください：",
            view=TitleInputView(self.flow)
        )


class RankSelectView(View):
    """対象ランク選択を含むView"""
    def __init__(self, flow: RecruitFlow, guild: discord.Guild):
        super().__init__()
        self.flow = flow
        self.add_item(RankSelect(flow, guild))


class TitleInputView(View):
    """タイトル入力ボタンを含むView"""
    def __init__(self, flow: RecruitFlow):
        super().__init__()
        self.flow = flow
        self.add_item(TitleInputButton(self.flow))

class TitleInputButton(discord.ui.Button):
    """タイトル入力用ボタン"""
    def __init__(self, flow: RecruitFlow):
        super().__init__(label="タイトルを入力", style=discord.ButtonStyle.primary)
        self.flow = flow

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id not in active_recruit_flows:
            await interaction.response.send_message("募集フローが見つかりませんでした。再度`!募集開始`コマンドを実行するか、募集開始ボタンを押してください。", ephemeral=True)
            return
        self.flow = active_recruit_flows[interaction.user.id]

        await interaction.response.send_modal(TitleModal(self.flow, interaction))


class TitleModal(discord.ui.Modal, title="募集タイトルを入力"):
    """募集タイトル入力用モーダル"""
    def __init__(self, flow: RecruitFlow, original_interaction: discord.Interaction):
        super().__init__()
        self.flow = flow
        self.original_interaction = original_interaction 
        self.title_input = TextInput(label="募集タイトル", placeholder="例：気軽にどうぞ！", max_length=100)
        self.add_item(self.title_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id not in active_recruit_flows:
            await interaction.response.send_message("募集フローが見つかりませんでした。再度`!募集開始`コマンドを実行するか、募集開始ボタンを押してください。", ephemeral=True)
            return
        self.flow = active_recruit_flows[interaction.user.id]

        self.flow.title = self.title_input.value

        try:
            await interaction.response.send_message("タイトルを受け付けました。", ephemeral=True)
        except discord.errors.InteractionResponded:
            logging.info("Interaction already responded to in TitleModal on_submit.")
        except Exception as e:
            logging.error(f"モーダル応答メッセージ送信中にエラー: {e}")

        try:
            await self.original_interaction.edit_original_response(
                content=f"タイトル入力が完了しました！ (`{self.flow.title}`)\n"
                        f"募集を作成しています...\n"
                        f"このメッセージはしばらくすると消えます。",
                view=None
            )
            logging.info(f"タイトル入力完了メッセージを更新しました: '{self.flow.title}'")
        except discord.errors.NotFound:
            logging.warning("元のインタラクションメッセージが見つからず、タイトル入力完了メッセージを更新できませんでした。")
        except Exception as e:
            logging.error(f"タイトル入力完了メッセージ更新中にエラー: {e}")

        bot.loop.create_task(create_vc_and_post_embed(self.original_interaction, self.flow))


async def create_vc_and_post_embed(interaction: discord.Interaction, flow: RecruitFlow):
    """VCを作成し、募集Embedを投稿する"""
    guild = interaction.guild
    category = guild.get_channel(VC_CATEGORY_ID)

    if not category:
        logging.error(f"VCカテゴリID {VC_CATEGORY_ID} が見つかりません。")
        try:
            await interaction.followup.send("VCカテゴリが見つかりませんでした。ボットの設定を確認してください。", ephemeral=True)
        except discord.errors.NotFound:
            logging.error("VCカテゴリ見つからないエラーメッセージを送信できませんでした (Webhook Unknown)。")
        if interaction.user.id in active_recruit_flows:
            del active_recruit_flows[interaction.user.id]
        return

    # VC権限設定
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=False),
        interaction.user: discord.PermissionOverwrite(connect=True, view_channel=True, speak=True),
    }

    # === VC名の変更ここから ===
    # 現在アクティブな募集の数を数える (募集番号のため)
    # active_recruit_flows には、募集主のIDがキーとして入っているので、その数を数える
    recruit_number = len(active_recruit_flows) # VCが作成される前に募集フローが追加されているはずなので、これまでの数 + 1 が現在の番号になります
    # 募集主のIDがactive_recruit_flowsに追加された後にこの関数が呼ばれるため、
    # 新しいVCの番号は現在のactive_recruit_flowsの長さと同じになる (1から始まるため)
    if interaction.user.id not in active_recruit_flows: # 念のため、もしこのフローがまだ追加されていなければ+1
        recruit_number += 1 

    # VC名に含めるゲームモードを決定
    game_mode_display = "不明"
    if flow.mode == "コンペ":
        game_mode_display = "コンペ"
    elif flow.mode == "アンレート":
        game_mode_display = "アンレート"

    # 新しいVC名
    new_vc_name = f"┣🔉・[{game_mode_display}]{recruit_number}番"
    # === VC名の変更ここまで ===

    try:
        vc_channel = await guild.create_voice_channel(
            name=new_vc_name, # ここを修正
            overwrites=overwrites,
            category=category
        )
        logging.info(f"VC '{vc_channel.name}' (ID: {vc_channel.id}) を作成しました。")
    except Exception as e:
        logging.error(f"VCの作成に失敗しました: {e}")
        try:
            await interaction.followup.send("VCの作成中にエラーが発生しました。時間を置いて再度お試しください。", ephemeral=True)
        except discord.errors.NotFound:
            logging.error("VC作成エラーメッセージを送信できませんでした (Webhook Unknown)。")
        if interaction.user.id in active_recruit_flows:
            del active_recruit_flows[interaction.user.id]
        return

    flow.vc_channel = vc_channel
    flow.participants.append(interaction.user)

    # メンションするロール
    mentions = [f"<@&{r}>" for r in flow.roles]

    # 募集Embedの作成
    embed = discord.Embed(
        title=flow.title,
        description=(
            f"**モード：** {flow.mode}\n"
            f"**募集人数：** あと{flow.people_to_recruit}人 (募集主を含め合計{flow.total_party_size}名)\n"
            f"**対象ランク：** {', '.join(mentions)}\n"
            f"**参加VC：** {vc_channel.mention}\n"
        ),
        color=discord.Color.green()
    )
    # 現在の参加者リストの表示
    embed.add_field(
        name=f"現在の参加者 ({len(flow.participants)}/{flow.total_party_size})",
        value=interaction.user.mention,
        inline=False
    )

    view = ParticipantView(flow)

    # 募集内容を投稿するチャンネルを取得
    recruit_post_channel = guild.get_channel(RECRUIT_POST_CHANNEL_ID)
    if not recruit_post_channel or not isinstance(recruit_post_channel, discord.TextChannel):
        logging.error(f"募集投稿チャンネルID {RECRUIT_POST_CHANNEL_ID} が見つからないか、テキストチャンネルではありません。")
        try:
            await interaction.followup.send("募集投稿チャンネルが見つかりませんでした。ボットの設定を確認してください。", ephemeral=True)
        except discord.errors.NotFound:
            logging.error("募集投稿チャンネル見つからないエラーメッセージを送信できませんでした (Webhook Unknown)。")
        if vc_channel:
            try:
                await vc_channel.delete()
            except Exception as vc_delete_e:
                logging.error(f"チャンネルエラー時のVC削除に失敗: {vc_delete_e}")
        if interaction.user.id in active_recruit_flows:
            del active_recruit_flows[interaction.user.id]
        return

    try:
        # Embedメッセージの送信は指定された募集投稿チャンネルへ
        message = await recruit_post_channel.send(content=" ".join(mentions), embed=embed, view=view)
        flow.message = message
        logging.info(f"募集Embedメッセージ (ID: {message.id}) をチャンネル {recruit_post_channel.name} に送信しました。")
        # 募集主に完了メッセージを送信
        await interaction.followup.send(f"募集が作成され、{recruit_post_channel.mention} に投稿されました！", ephemeral=True)

    except discord.errors.NotFound as e:
        logging.error(f"募集Embedの送信に失敗しました: {e} (Webhook Unknown)。チャンネルIDが正しいか確認してください。")
        if vc_channel:
            try:
                await vc_channel.edit(name=f"❌エラーVC-{interaction.user.display_name}") 
                logging.info(f"エラーのためVC名を '{vc_channel.name}' に変更しました。")
                await interaction.followup.send("募集メッセージの送信に失敗しました。VCは作成されましたが、手動でご確認ください。", ephemeral=True)
            except Exception as vc_edit_e:
                logging.error(f"VC名変更に失敗しました: {vc_edit_e}")
        if interaction.user.id in active_recruit_flows:
            del active_recruit_flows[interaction.user.id]
        return
    except Exception as e:
        logging.error(f"募集Embedの送信中に予期せぬエラー: {e}")
        if vc_channel:
            try:
                await vc_channel.edit(name=f"❌エラーVC-{interaction.user.display_name}") 
                logging.info(f"エラーのためVC名を '{vc_channel.name}' に変更しました。")
                await interaction.followup.send("募集メッセージの送信中にエラーが発生しました。VCは作成されましたが、手動でご確認ください。", ephemeral=True)
            except Exception as vc_edit_e:
                logging.error(f"VC名変更に失敗しました: {vc_edit_e}")
        if interaction.user.id in active_recruit_flows:
            del active_recruit_flows[interaction.user.id]
        return

    flow.vc_check_task = bot.loop.create_task(monitor_vc_for_empty(flow))
    logging.info(f"VC監視タスクをVC ID {flow.vc_channel.id} に対して起動しました。")


class ParticipantView(View):
    """参加ボタン、離脱ボタン、募集停止ボタンを含むView"""
    def __init__(self, flow: RecruitFlow):
        super().__init__(timeout=None)
        self.flow = flow

    async def update_embed(self):
        """Embedの参加者情報を更新するヘルパー関数"""
        if not self.flow.message:
            logging.warning("募集メッセージが見つからないためEmbedを更新できません。")
            return

        if self.flow.participants and self.flow.participants[0].id in active_recruit_flows:
            self.flow = active_recruit_flows[self.flow.participants[0].id]
        else:
            logging.warning("募集フローが見つからないためEmbedを更新できません。")
            return

        embed = self.flow.message.embeds[0]

        current_participants_text = "\n".join([member.mention for member in self.flow.participants])
        if not current_participants_text:
            current_participants_text = "現在参加者はいません。"

        embed.set_field_at(
            index=0, 
            name=f"現在の参加者 ({len(self.flow.participants)}/{self.flow.total_party_size})",
            value=current_participants_text,
            inline=False
        )

        remaining_slots = self.flow.total_party_size - len(self.flow.participants)
        remaining_slots = max(0, remaining_slots) 

        embed.description = (
            f"**モード：** {self.flow.mode}\n"
            f"**募集人数：** あと{remaining_slots}人 (募集主を含め合計{self.flow.total_party_size}名)\n"
            f"**対象ランク：：** {', '.join([f'<@&{r}>' for r in self.flow.roles])}\n"
            f"**参加VC：：** {self.flow.vc_channel.mention}\n"
        )

        if len(self.flow.participants) >= self.flow.total_party_size:
            # 参加ボタンを無効化
            for item in self.children:
                if isinstance(item, Button) and item.custom_id == "join_button":
                    item.disabled = True
                    break
            logging.info(f"募集が満員になりました。参加ボタンを無効化。")
        else:
              # 参加ボタンを有効化
            for item in self.children:
                if isinstance(item, Button) and item.custom_id == "join_button":
                    item.disabled = False
                    break

        await self.flow.message.edit(embed=embed, view=self)


    @discord.ui.button(label="✅ 参加する", style=discord.ButtonStyle.primary, custom_id="join_button")
    async def join(self, interaction: discord.Interaction, button: Button):
        recruiter_id = self.flow.participants[0].id if self.flow.participants else None

        if recruiter_id and recruiter_id in active_recruit_flows:
            self.flow = active_recruit_flows[recruiter_id]
        else:
            await interaction.response.send_message("この募集はすでに終了したか、募集主が募集中ではありません。", ephemeral=True)
            return

        if interaction.user == self.flow.participants[0]:
            await interaction.response.send_message("あなたは募集主です。参加ボタンを押す必要はありません。", ephemeral=True)
            return

        if interaction.user in self.flow.participants:
            await interaction.response.send_message("すでに募集に参加しています。", ephemeral=True)
            return

        if len(self.flow.participants) >= self.flow.total_party_size:
            await interaction.response.send_message("募集人数の上限に達しています。", ephemeral=True)
            await self.update_embed() 
            return

        self.flow.participants.append(interaction.user)
        logging.info(f"{interaction.user.display_name} が募集に参加しました。")

        try:
            vc_channel_check = bot.get_channel(self.flow.vc_channel.id)
            if vc_channel_check:
                await vc_channel_check.set_permissions(
                    interaction.user,
                    connect=True,
                    view_channel=True,
                    speak=True
                )
                logging.info(f"{interaction.user.display_name} にVC権限を付与しました。")
            else:
                logging.warning(f"VC ID {self.flow.vc_channel.id} が見つからないためVC権限を付与できませんでした。")
                await interaction.response.send_message("VCが見つからないため、VC権限の付与に失敗しました。手動でVCに入ってください。", ephemeral=True)
                await self.update_embed()
                return
        except Exception as e:
            logging.error(f"VC権限付与に失敗しました: {e}")
            await interaction.response.send_message("VC権限の付与中にエラーが発生しました。手動でVCに入ってください。", ephemeral=True)

        await self.update_embed()
        await interaction.response.send_message("募集に参加しました！", ephemeral=True)

    @discord.ui.button(label="❌ 離脱する", style=discord.ButtonStyle.danger, custom_id="leave_button")
    async def leave(self, interaction: discord.Interaction, button: Button):
        recruiter_id = self.flow.participants[0].id if self.flow.participants else None

        if recruiter_id and recruiter_id in active_recruit_flows:
            self.flow = active_recruit_flows[recruiter_id]
        else:
            await interaction.response.send_message("この募集はすでに終了したか、募集主が募集中ではありません。", ephemeral=True)
            return

        if interaction.user not in self.flow.participants:
            await interaction.response.send_message("募集に参加していません。", ephemeral=True)
            return

        if interaction.user == self.flow.participants[0]:
            await interaction.response.send_message("募集主は募集を離脱できません。募集を停止するには「🚫 募集停止」ボタンを押すか、VCから退出してください。", ephemeral=True)
            return

        self.flow.participants.remove(interaction.user)
        logging.info(f"{interaction.user.display_name} が募集から離脱しました。")

        try:
            vc_channel_check = bot.get_channel(self.flow.vc_channel.id)
            if vc_channel_check:
                await vc_channel_check.set_permissions(
                    interaction.user,
                    connect=False,
                    view_channel=False,
                    speak=False
                )
                logging.info(f"{interaction.user.display_name} からVC権限を剥奪しました。")
            else:
                logging.warning(f"VC ID {self.flow.vc_channel.id} が見つからないためVC権限を剥奪できませんでした。")
        except Exception as e:
            logging.error(f"VC権限剥奪に失敗しました: {e}")
            await interaction.response.send_message("VC権限の剥奪中にエラーが発生しました。", ephemeral=True)

        await self.update_embed()
        await interaction.response.send_message("募集から離脱しました。", ephemeral=True)

    @discord.ui.button(label="🚫 募集停止", style=discord.ButtonStyle.red, custom_id="stop_recruit_button")
    async def stop_recruit(self, interaction: discord.Interaction, button: Button):
        recruiter_id = self.flow.participants[0].id if self.flow.participants else None

        if not recruiter_id or recruiter_id not in active_recruit_flows:
            await interaction.response.send_message("この募集はすでに終了しているか、募集主が募集中ではありません。", ephemeral=True)
            return

        # 募集主のみが停止できる
        if interaction.user.id != recruiter_id:
            await interaction.response.send_message("募集を停止できるのは募集主のみです。", ephemeral=True)
            return

        await interaction.response.send_message("募集を停止しています...", ephemeral=True)
        logging.info(f"募集主 {interaction.user.display_name} が募集停止ボタンを押しました。")

        await end_recruit_flow(interaction.user.id) # ヘルパー関数を呼び出す
        await interaction.followup.send("募集を停止し、関連リソースを削除しました。", ephemeral=True)


async def end_recruit_flow(recruiter_id: int):
    """募集フローを終了させ、関連リソース（VC、メッセージ、タスク）をクリーンアップするヘルパー関数"""
    if recruiter_id not in active_recruit_flows:
        return 

    flow_to_end = active_recruit_flows[recruiter_id]

    try:
        if flow_to_end.vc_check_task and not flow_to_end.vc_check_task.done():
            flow_to_end.vc_check_task.cancel()
            try:
                await flow_to_end.vc_check_task
            except asyncio.CancelledError:
                pass
            logging.info(f"VC監視タスク for VC ID {flow_to_end.vc_channel.id if flow_to_end.vc_channel else 'N/A'} を停止しました。")

        if flow_to_end.vc_channel:
            vc_channel_to_delete = bot.get_channel(flow_to_end.vc_channel.id)
            if vc_channel_to_delete:
                await vc_channel_to_delete.delete()
                logging.info(f"VC {vc_channel_to_delete.name} (ID: {vc_channel_to_delete.id}) を削除しました。")
            flow_to_end.vc_channel = None

        if flow_to_end.message:
            try:
                message_to_edit = await flow_to_end.message.channel.fetch_message(flow_to_end.message.id)
                embed = message_to_edit.embeds[0]
                embed.title = f"[終了] {embed.title}"
                embed.color = discord.Color.dark_grey()
                embed.description += "\n\n**この募集は終了しました。**"
                await message_to_edit.edit(embed=embed, view=None)
            except discord.NotFound:
                logging.warning(f"募集メッセージ ID {flow_to_end.message.id} が見つかりませんでした。")
                pass
            flow_to_end.message = None

        del active_recruit_flows[recruiter_id]
        logging.info(f"募集主 {recruiter_id} のアクティブ募集をactive_recruit_flowsから削除しました。")

    except Exception as e:
        logging.error(f"募集フロー終了中にエラーが発生しました（ID: {recruiter_id}）: {e}")
        if recruiter_id in active_recruit_flows:
            del active_recruit_flows[recruiter_id]


async def monitor_vc_for_empty(flow: RecruitFlow):
    """特定のVCが空になったら削除するコルーチン"""
    try:
        while True:
            channel = bot.get_channel(flow.vc_channel.id) if flow.vc_channel else None

            if not channel:
                logging.info(f"VC {flow.vc_channel.id if flow.vc_channel else 'N/A'} が見つかりません。監視を停止します。")
                break

            if not channel.members:
                logging.info(f"VC {channel.name} (ID: {channel.id}) が空です。300秒後に再確認します。")
                await asyncio.sleep(300)

                channel = bot.get_channel(flow.vc_channel.id) if flow.vc_channel else None
                if not channel or not channel.members:
                    logging.info(f"VC {flow.vc_channel.name} (ID: {flow.vc_channel.id}) が引き続き空のため削除します。")

                    if flow.participants and flow.participants[0].id in active_recruit_flows:
                        await end_recruit_flow(flow.participants[0].id)
                    else:
                        try:
                            await channel.delete()
                            logging.info(f"孤立VC {channel.name} (ID: {channel.id}) を削除しました。")
                        except Exception as e:
                            logging.error(f"孤立VC削除中にエラー: {e}")

                        if flow.message:
                            try:
                                message_to_edit = await flow.message.channel.fetch_message(flow.message.id)
                                embed = message_to_edit.embeds[0]
                                embed.title = f"[終了] {embed.title}"
                                embed.color = discord.Color.red()
                                embed.description += "\n\n**この募集はVCが空になったため終了しました。**"
                                await message_to_edit.edit(embed=embed, view=None)
                            except discord.NotFound:
                                logging.warning(f"孤立募集メッセージ ID {flow.message.id} が見つかりませんでした。")
                                pass
                        break

            await asyncio.sleep(30)

    except asyncio.CancelledError:
        logging.info(f"VC監視タスク for VC {flow.vc_channel.name if flow.vc_channel else 'N/A'} (ID: {flow.vc_channel.id if flow.vc_channel else 'N/A'}) がキャンセルされました。")
    except Exception as e:
        logging.error(f"Error in monitor_vc_for_empty for VC {flow.vc_channel.id if flow.vc_channel else 'N/A'}: {e}")


@bot.command()
async def 募集開始(ctx):
    """募集フローを開始するための準備コマンド（募集開始ボタンを押すよう促す）"""
    if ctx.author.id in active_recruit_flows:
        await ctx.send("現在、あなたは募集フローを開始しています。前の募集を完了またはキャンセルしてください。", ephemeral=True)
        return

    button_channel = bot.get_channel(RECRUIT_BUTTON_CHANNEL_ID)
    if not button_channel:
        await ctx.send(f"募集開始ボタンを設置するチャンネル (ID: {RECRUIT_BUTTON_CHANNEL_ID}) が見つかりません。ボットの設定を確認してください。", ephemeral=True)
        return
    if not isinstance(button_channel, discord.TextChannel):
        await ctx.send(f"募集開始ボタンを設置するチャンネル (ID: {RECRUIT_BUTTON_CHANNEL_ID}) はテキストチャンネルではありません。", ephemeral=True)
        return

    flow = RecruitFlow()
    active_recruit_flows[ctx.author.id] = flow
    logging.info(f"募集主 {ctx.author.display_name} の新しい募集フロー (ID: {ctx.author.id}) をactive_recruit_flowsに登録しました。")

    await ctx.send(f"募集を開始するには、{button_channel.mention} にある「📢 募集を開始」ボタンを押してください！", ephemeral=True)


class RecruitButtonView(View):
    """募集開始ボタンを含むView"""
    def __init__(self): 
        super().__init__()

    @discord.ui.button(label="📢 募集を開始", style=discord.ButtonStyle.success, custom_id="start_recruit_button")
    async def start(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id in active_recruit_flows:
            await interaction.response.send_message("現在、あなたは募集フローを開始しています。前の募集を完了またはキャンセルしてください。", ephemeral=True)
            return

        flow = RecruitFlow()
        active_recruit_flows[interaction.user.id] = flow
        logging.info(f"募集主 {interaction.user.display_name} の新しい募集フローをボタンから開始し、active_recruit_flowsに登録しました。")

        await interaction.response.send_message("ゲームモードを選択してください：", view=ModeSelectView(flow), ephemeral=True)


@bot.command()
async def 募集キャンセル(ctx):
    """進行中の募集をキャンセルするコマンド"""
    if ctx.author.id not in active_recruit_flows:
        await ctx.send("現在、あなたには進行中の募集がありません。", ephemeral=True)
        return

    await ctx.send("進行中の募集をキャンセルしています...", ephemeral=True)
    logging.info(f"募集主 {ctx.author.display_name} が募集キャンセルコマンドを実行しました。")

    await end_recruit_flow(ctx.author.id) # ヘルパー関数を呼び出す
    await ctx.send("進行中の募集をキャンセルし、関連リソースを削除しました。", ephemeral=True)

@bot.tree.command(name="募集終了", description="現在進行中の募集を終了します。")
async def end_recruit(interaction: discord.Interaction):
    """
    スラッシュコマンド: 募集主が自分の募集を終了する
    """
    user_id = interaction.user.id
    if user_id not in active_recruit_flows:
        await interaction.response.send_message("現在、あなたには進行中の募集がありません。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True) 
    logging.info(f"募集主 {interaction.user.display_name} がスラッシュコマンド /募集終了 を実行しました。")

    try:
        await end_recruit_flow(user_id)
        await interaction.followup.send("募集を終了し、関連リソースを削除しました。", ephemeral=True)
    except Exception as e:
        logging.error(f"スラッシュコマンドでの募集終了中にエラーが発生しました（ユーザーID: {user_id}）: {e}")
        await interaction.followup.send("募集の終了中にエラーが発生しました。時間を置いて再度お試しください。", ephemeral=True)


@bot.command()
@commands.is_owner()
async def 募集強制終了(ctx, user_id: int):
    """
    管理者用: 特定のユーザーの募集フローを強制的に終了させるコマンド。
    使用例: !募集強制終了 123456789012345678
    """
    target_user = bot.get_user(user_id)
    if not target_user:
        await ctx.send(f"ユーザーID {user_id} のユーザーが見つかりませんでした。", ephemeral=True)
        return

    if user_id not in active_recruit_flows:
        await ctx.send(f"ユーザー {target_user.display_name} (ID: {user_id}) にはアクティブな募集がありません。", ephemeral=True)
        return

    await ctx.send(f"ユーザー {target_user.display_name} (ID: {user_id}) の募集フローを強制終了します。", ephemeral=True)
    logging.warning(f"管理者 {ctx.author.display_name} がユーザー {target_user.display_name} (ID: {user_id}) の募集フローを強制終了しました。")

    await end_recruit_flow(user_id) # ヘルパー関数を呼び出す
    await ctx.send(f"ユーザー {target_user.display_name} の募集フローを強制終了し、関連リソースを削除しました。", ephemeral=True)


@bot.command()
@commands.is_owner()
async def 停止(ctx):
    """ボットをオフラインにするコマンド"""
    logging.info(f"{ctx.author.display_name} がボット停止コマンドを実行しました。")
    await ctx.send("🔴 Botをオフラインにします。")

    # 全てのアクティブな募集フローを終了させる
    for recruiter_id in list(active_recruit_flows.keys()): 
        await end_recruit_flow(recruiter_id)
    logging.info("全ての募集フローとVC監視タスクを停止しました。")

    # 新しく追加するステータス更新タスクも停止
    if hasattr(bot, 'status_update_task') and bot.status_update_task.is_running():
        bot.status_update_task.cancel()
        try:
            await bot.status_update_task
        except asyncio.CancelledError:
            pass
        logging.info("ステータス更新タスクを停止しました。")

    if bot.start_button_task.is_running():
        bot.start_button_task.cancel()
        try:
            await bot.start_button_task
        except asyncio.CancelledError:
            pass
        logging.info("募集開始ボタン更新タスクを停止しました。")

    await bot.close()

@bot.command()
async def ping(ctx):
    """ボットの応答を確認するコマンド"""
    await ctx.send(f"🏓 Pong! Bot Latency: {round(bot.latency * 1000)}ms")


@tasks.loop(minutes=10)
async def update_bot_status():
    """ボットのステータスを定期的に更新し、Replitの活性状態を保つタスク"""
    try:
        current_time = datetime.now().strftime("%H:%M")
        await bot.change_presence(activity=discord.Game(name=f"稼働中 | {current_time}"))
        logging.info(f"Botステータスを更新しました: '稼働中 | {current_time}'")
    except Exception as e:
        logging.error(f"Botステータス更新中にエラーが発生しました: {e}")


@tasks.loop(seconds=START_BUTTON_UPDATE_INTERVAL)
async def manage_start_button_message():
    """
    募集開始ボタンのメッセージを、指定されたチャンネルに常に存在するように管理するタスク。
    メッセージが削除された場合は再投稿し、存在する場合は編集のみを行う。
    """
    logging.info("募集開始ボタンのメッセージ管理タスクを実行中...")

    channel = bot.get_channel(RECRUIT_BUTTON_CHANNEL_ID)
    if not channel or not isinstance(channel, discord.TextChannel):
        logging.error(f"募集開始ボタンチャンネル (ID: {RECRUIT_BUTTON_CHANNEL_ID}) が見つからないか、テキストチャンネルではありません。")
        return

    message_id = start_button_message_info.get(channel.id)
    view = RecruitButtonView() 

    if message_id:
        try:
            message_to_edit = await channel.fetch_message(message_id)
            await message_to_edit.edit(content="募集を開始するには以下のボタンを押してください：", view=view)
            logging.info(f"既存の募集開始メッセージ (ID: {message_id}) を更新しました。")
        except discord.NotFound:
            logging.warning(f"募集開始メッセージ (ID: {message_id}) がチャンネル {channel.name} から見つかりません。再投稿します。")
            new_message = await channel.send("募集を開始するには以下のボタンを押してください：", view=view)
            start_button_message_info[channel.id] = new_message.id
            logging.info(f"募集開始メッセージを新規送信しました。(ID: {new_message.id})")
        except Exception as e:
            logging.error(f"既存の募集開始メッセージ更新中に予期せぬエラー: {e}")
            try:
                new_message = await channel.send("募集を開始するには以下のボタンを押してください：", view=view)
                start_button_message_info[channel.id] = new_message.id
                logging.info(f"募集開始メッセージを新規送信しました。(ID: {new_message.id}) (エラー回復)")
            except Exception as e_resend:
                logging.error(f"募集開始メッセージ再送信中にエラー: {e_resend}")
    else:
        try:
            new_message = await channel.send("募集を開始するには以下のボタンを押してください：", view=view)
            start_button_message_info[channel.id] = new_message.id
            logging.info(f"募集開始メッセージを新規送信しました。(ID: {new_message.id})")
        except Exception as e:
            logging.error(f"募集開始メッセージの初回送信中にエラー: {e}")


@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    logging.info(f'Guilds: {len(bot.guilds)}')

    # 募集開始ボタン管理タスクを開始
    if not hasattr(bot, 'start_button_task') or not bot.start_button_task.is_running():
        bot.start_button_task = manage_start_button_message
        bot.start_button_task.start()
        logging.info("募集開始ボタン管理タスクを開始しました。")

    # ステータス更新タスクを開始
    if not hasattr(bot, 'status_update_task') or not bot.status_update_task.is_running():
        bot.status_update_task = update_bot_status
        bot.status_update_task.start()
        logging.info("ステータス更新タスクを開始しました。")

    # スラッシュコマンドを同期する
    try:
        synced = await bot.tree.sync() 
        logging.info(f"スラッシュコマンド {len(synced)} 件を同期しました。")
        for command in synced:
            logging.info(f" - / {command.name}")
    except Exception as e:
        logging.error(f"スラッシュコマンドの同期中にエラーが発生しました: {e}")

bot.run(os.environ['DISCORD_BOT_TOKEN'])

