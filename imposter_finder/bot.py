from __future__ import annotations

import asyncio
import io

try:
    import discord
    from discord import app_commands
    from discord.ext import commands
except ImportError:
    discord = None
    app_commands = None
    commands = None

from imposter_finder.analysis.pubg_player import DEFAULT_MATCH_COUNT, analyze_pubg_player
from imposter_finder.collector import refresh_player_history, run_collection_cycle
from imposter_finder.config import Settings
from imposter_finder.games.pubg import PubgClient
from imposter_finder.healthcheck import ping as ping_healthcheck
from imposter_finder.registry import PubgPlayer, load_pubg_players
from imposter_finder.radar import render_radar_png
from imposter_finder.storage import LocalStore


def run_discord_bot(settings: Settings, serve: bool = False) -> None:
    if discord is None or app_commands is None or commands is None:
        raise RuntimeError("Discord 명령 봇에는 discord.py가 필요합니다. `pip install -r requirements.txt`를 실행하세요.")

    if not settings.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is required to run the Discord command bot")

    players = load_pubg_players(settings.players_path)
    by_alias = _player_aliases(players)
    client = PubgClient(settings.pubg_api_key)
    store = LocalStore(settings.local_database_path) if serve else None

    class ImposterFinderBot(commands.Bot):
        collector_task: asyncio.Task[None] | None = None

        async def setup_hook(self) -> None:
            if settings.discord_guild_id:
                guild = discord.Object(id=int(settings.discord_guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
            if serve and store:
                self.collector_task = asyncio.create_task(_collection_loop(settings, store), name="pubg-collector")

        async def close(self) -> None:
            if self.collector_task:
                self.collector_task.cancel()
                try:
                    await self.collector_task
                except asyncio.CancelledError:
                    pass
            if store:
                store.close()
            await super().close()

    bot = ImposterFinderBot(command_prefix="!", intents=discord.Intents.default())

    async def registered_player_autocomplete(_: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        query = current.casefold()
        matches = [
            player
            for player in players
            if query in player.name.casefold() or query in player.nickname.casefold()
        ]
        return [
            app_commands.Choice(name=f"{player.name} · {player.nickname}", value=player.name)
            for player in matches[:25]
        ]

    @bot.tree.command(name="user", description="등록된 사용자의 PUBG 최근 전적을 분석합니다.")
    @app_commands.describe(이름="등록된 이름을 입력하세요. 입력하면 후보가 표시됩니다.", 판수="기본 30판, 5~100판")
    @app_commands.autocomplete(이름=registered_player_autocomplete)
    async def user(interaction: discord.Interaction, 이름: str, 판수: app_commands.Range[int, 5, 100] = DEFAULT_MATCH_COUNT) -> None:
        player = by_alias.get(이름.casefold())
        if not player:
            known = ", ".join(sorted({item.name for item in players}))
            await interaction.response.send_message(f"등록된 PUBG 플레이어만 조회할 수 있어요. 등록 이름: {known}", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            report = await asyncio.to_thread(_load_report, client, player, 판수, store)
        except Exception as exc:
            await interaction.followup.send(f"전적을 가져오지 못했습니다: `{exc}`", ephemeral=True)
            return

        color = {"S": 0xF1C40F, "A": 0x2ECC71, "B": 0x3498DB, "C": 0xE67E22, "E": 0xE74C3C, "F": 0x992D22}.get(report["grade"], 0x95A5A6)
        stats = report["stats"]
        axes = " · ".join(f"{name} {value}" for name, value in report["axes"].items())
        embed = discord.Embed(
            title=f"🕵️ 전적 감정서 — {player.name}",
            description=f"**{report['grade']} 등급** · 컨디션 점수 **{report['score']}점**\n{report['sample_note']}",
            color=color,
        )
        embed.add_field(name="최근 성적", value=(f"승률 **{stats['win_rate']}%** · 평균 **{stats['avg_damage']}딜** · **{stats['avg_kills']}킬**\n"
            f"평균 생존 **{stats['avg_survival_minutes']}분** · 평균 순위 **{stats['avg_placement']}위**"), inline=False)
        embed.add_field(name="플레이 성향", value=" · ".join(report["traits"]), inline=False)
        embed.add_field(name="레이더 지표 (0–100)", value=axes, inline=False)
        embed.add_field(name="판정", value=report["verdict"], inline=False)
        embed.set_footer(text="범인찾기 전적 감정서 · 재미와 최근 공개 전적을 바탕으로 한 참고용 분석")
        chart = discord.File(io.BytesIO(render_radar_png(report["axes"])), filename="pubg-radar.png")
        embed.set_image(url="attachment://pubg-radar.png")
        await interaction.followup.send(embed=embed, file=chart)

    bot.run(settings.discord_bot_token)


def _player_aliases(players: list[PubgPlayer]) -> dict[str, PubgPlayer]:
    aliases: dict[str, PubgPlayer] = {}
    for player in players:
        aliases[player.name.casefold()] = player
        aliases[player.nickname.casefold()] = player
    return aliases


def _load_report(client: PubgClient, player: PubgPlayer, match_count: int, store: LocalStore | None = None) -> dict:
    matches = store.recent_player_matches(player.platform, player.nickname, match_count) if store else []
    if len(matches) < match_count:
        if store:
            refresh_player_history(client, store, player, match_count)
            matches = store.recent_player_matches(player.platform, player.nickname, match_count)
        else:
            match_ids = client.get_recent_match_ids(player.platform, player.nickname, match_count)
            matches = [client.get_match(player.platform, match_id) for match_id in match_ids]
    return analyze_pubg_player(player.nickname, matches, match_count)


async def _collection_loop(settings: Settings, store: LocalStore) -> None:
    """Bootstrap quietly, then publish new group matches every configured interval."""
    first_cycle = True
    while True:
        try:
            result = await asyncio.to_thread(run_collection_cycle, settings, store, not first_cycle)
            print(f"[collector] matches={result['matches']} published={result['published']} bootstrap={first_cycle}")
            await asyncio.to_thread(ping_healthcheck, settings.healthcheck_imposter_finder_url)
            first_cycle = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[collector] ERROR: {exc}")
            await asyncio.to_thread(ping_healthcheck, settings.healthcheck_imposter_finder_url, True)
        await asyncio.sleep(max(settings.pubg_poll_interval_minutes, 1) * 60)
