"""Lean Docker 실행기

Docker 컨테이너로 Lean 백테스트 직접 실행. (Lean CLI 불필요)
"""

import json
import logging
import os
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .project_manager import LeanProject

logger = logging.getLogger(__name__)

# Lean Docker 이미지
LEAN_IMAGE = "quantconnect/lean:latest"

# LEAN 데이터 읽기 작업 스레드 수. 여러 스레드가 Python 커스텀 데이터(Reader)를 동시에 부르면
# 워밍업 시작 직후("Launching analysis ... Warmup: True") 교착으로 멈추는 일이 있다
# (실측: 동시 16개 기본값 9개 멈춤 → 1로 두면 32개 중 0개). Python Reader는 어차피 GIL 때문에 한 번에 하나라 느려지지 않는다.
DATA_FEED_WORKERS = int(os.getenv("LEAN_DATA_FEED_WORKERS", "1"))


@dataclass
class LeanRun:
    """Lean 백테스트 실행 결과"""
    project: LeanProject
    success: bool
    output_dir: Path
    raw_result: Optional[Dict] = None
    error: Optional[str] = None
    duration_seconds: float = 0
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    
    @property
    def result_json(self) -> Optional[Path]:
        """결과 JSON 파일 경로"""
        if not self.output_dir.exists():
            return None
        
        # Algorithm.json이 메인 결과 파일
        main_result = self.output_dir / "Algorithm.json"
        if main_result.exists():
            return main_result
        
        # 폴백: order, log, summary, monitor 제외한 첫 번째 json
        for f in self.output_dir.glob("*.json"):
            name_lower = f.name.lower()
            if not any(x in name_lower for x in ["order", "log", "summary", "monitor"]):
                return f
        
        return None
    
    @property
    def orders_json(self) -> Optional[Path]:
        """주문 내역 JSON 파일 경로"""
        if not self.output_dir.exists():
            return None
        
        for f in self.output_dir.glob("*order*.json"):
            return f
        
        return None
    
    @property
    def log_txt(self) -> Optional[Path]:
        """로그 파일 경로"""
        if not self.output_dir.exists():
            return None
        
        for f in self.output_dir.glob("*.log"):
            return f
        for f in self.output_dir.glob("*log*.txt"):
            return f
        
        return None
    
    def load_result(self) -> Dict:
        """결과 JSON 로드"""
        if self.raw_result:
            return self.raw_result
        
        result_file = self.result_json
        if result_file and result_file.exists():
            self.raw_result = json.loads(result_file.read_text())
            return self.raw_result
        
        return {}
    
    def get_statistics(self) -> Dict[str, Any]:
        """통계 추출"""
        result = self.load_result()
        return result.get("statistics", {})
    
    def get_trades(self) -> List[Dict]:
        """거래 내역 추출"""
        result = self.load_result()
        orders = result.get("orders", {})
        return list(orders.values()) if isinstance(orders, dict) else []
    
    def get_equity_curve(self) -> Dict[str, float]:
        """자산 곡선 추출"""
        result = self.load_result()
        charts = result.get("charts", {})
        
        strategy_equity = charts.get("Strategy Equity", {})
        series = strategy_equity.get("series", {})
        equity_series = series.get("Equity", {})
        values = equity_series.get("values", [])
        
        # Lean format: [timestamp, open, high, low, close]
        return {
            str(point[0]): point[4] if len(point) > 4 else point[1]
            for point in values if isinstance(point, list) and len(point) >= 2
        }


class LeanExecutor:
    """Lean Docker 실행기 (Lean CLI 불필요)"""
    
    @classmethod
    def run(
        cls,
        project: LeanProject,
        stream_logs: bool = False,
        timeout: float = 600,
        data_path: Optional[Path] = None,
        container_name: Optional[str] = None,
    ) -> LeanRun:
        """Docker로 Lean 백테스트 실행
        
        Args:
            project: Lean 프로젝트
            stream_logs: 로그 스트리밍 여부
            timeout: 타임아웃 (초)
            data_path: /Data 로 붙일 데이터 폴더 (기본: 워크스페이스 data). 하이퍼옵트는 전용 폴더를 쓴다.
            container_name: 컨테이너 이름 (기본: run_id로 만든다). 호출 쪽에서 직접 멈출 때 쓴다.
        
        Returns:
            LeanRun 결과 객체
        
        Raises:
            RuntimeError: 실행 실패 시
        """
        started_at = datetime.now()
        output_dir = project.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # 경로 (절대 경로로 변환)
        workspace = project.project_dir.parent.parent.resolve()
        project_path = project.project_dir.resolve()
        data_path = (data_path or workspace / "data").resolve()
        results_path = output_dir.resolve()
        
        # 디버깅: 경로 확인
        logger.info(f"[Lean] workspace: {workspace}")
        logger.info(f"[Lean] project_path: {project_path}")
        logger.info(f"[Lean] data_path: {data_path}")
        logger.info(f"[Lean] results_path: {results_path}")
        
        # 데이터 폴더 존재 확인
        if not data_path.exists():
            raise RuntimeError(f"데이터 폴더가 없습니다: {data_path}")
        
        symbol_props = data_path / "symbol-properties" / "symbol-properties-database.csv"
        if not symbol_props.exists():
            raise RuntimeError(f"symbol-properties-database.csv가 없습니다. setup_lean_data.sh 실행 필요: {symbol_props}")
        
        # Lean config.json 생성
        lean_config = {
            "algorithm-type-name": "Algorithm",
            "algorithm-language": "Python",
            "algorithm-location": "/Algorithm/main.py",
            "data-folder": "/Data",
            "results-destination-folder": "/Results",
            "log-handler": "ConsoleLogHandler",
            "messaging-handler": "QuantConnect.Messaging.Messaging",
            "job-queue-handler": "QuantConnect.Queues.JobQueue",
            "api-handler": "QuantConnect.Api.Api",
            "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
            "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
            "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
            "object-store": "QuantConnect.Lean.Engine.Storage.LocalObjectStore",
            "data-aggregator": "QuantConnect.Lean.Engine.DataFeeds.AggregationManager",
            "data-feed-workers-count": DATA_FEED_WORKERS,
            "environments": {
                "backtesting": {
                    "live-mode": False,
                    "setup-handler": "QuantConnect.Lean.Engine.Setup.BacktestingSetupHandler",
                    "result-handler": "QuantConnect.Lean.Engine.Results.BacktestingResultHandler",
                    "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.FileSystemDataFeed",
                    "real-time-handler": "QuantConnect.Lean.Engine.RealTime.BacktestingRealTimeHandler",
                    "history-provider": "QuantConnect.Lean.Engine.HistoricalData.SubscriptionDataReaderHistoryProvider",
                    "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler"
                }
            },
            "environment": "backtesting"
        }
        
        config_path = project_path / "lean-config.json"
        config_path.write_text(json.dumps(lean_config, indent=2))
        
        # Docker 명령어 구성
        # 타임아웃 때 docker 클라이언트만 죽으면 컨테이너는 계속 돌므로, 이름을 붙여 직접 멈춘다
        container_name = container_name or cls.container_name_for(project.run_id)
        cmd = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--label", "com.open-trading-api.component=backtester-lean",
            "-v", f"{project_path}:/Algorithm:ro",
            "-v", f"{data_path}:/Data:ro",
            "-v", f"{results_path}:/Results",
            "-v", f"{config_path}:/Lean/Launcher/bin/Debug/config.json:ro",
            LEAN_IMAGE,
        ]
        
        logger.info(f"[Lean] Docker 실행: {project.run_id}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            
            finished_at = datetime.now()
            duration = (finished_at - started_at).total_seconds()
            stdout = result.stdout + result.stderr
            
            if result.returncode != 0:
                error_msg = f"Lean 백테스트 실패 (exit code: {result.returncode})\n{stdout[-2000:]}"
                logger.error(f"[Lean] {error_msg}")
                raise RuntimeError(error_msg)
            
            # 성공 결과 생성
            run = LeanRun(
                project=project,
                success=True,
                output_dir=output_dir,
                duration_seconds=duration,
                started_at=started_at,
                finished_at=finished_at,
            )
            
            run.load_result()
            
            logger.info(f"[Lean] 완료: {duration:.1f}초")
            return run
            
        except subprocess.TimeoutExpired:
            cls._kill_container(container_name)
            error_msg = f"Lean 백테스트 타임아웃 ({timeout}초)"
            logger.error(f"[Lean] {error_msg}")
            raise RuntimeError(error_msg)
        
        except FileNotFoundError:
            error_msg = "Docker가 설치되지 않았습니다."
            logger.error(f"[Lean] {error_msg}")
            raise RuntimeError(error_msg)
    
    @staticmethod
    def container_name_for(run_id: str) -> str:
        """실행 ID로 겹치지 않는 컨테이너 이름을 만든다."""
        return "lean-" + re.sub(r"[^A-Za-z0-9_.-]", "-", run_id)[:80] + "-" + uuid.uuid4().hex[:6]

    @staticmethod
    def _kill_container(name: str, reason: str = "타임아웃") -> None:
        """LEAN 컨테이너를 멈춘다 (--rm 이라 멈추면 자동 삭제)."""
        try:
            subprocess.run(["docker", "kill", name], capture_output=True, text=True, timeout=30)
            logger.warning(f"[Lean] {reason} 컨테이너 정지: {name}")
        except (OSError, subprocess.SubprocessError) as e:
            logger.error(f"[Lean] 컨테이너 정지 실패 ({name}): {e}")

    @classmethod
    def pull_image(cls) -> bool:
        """Lean Docker 이미지 다운로드"""
        try:
            logger.info(f"[Lean] Docker 이미지 다운로드 중: {LEAN_IMAGE}")
            result = subprocess.run(
                ["docker", "pull", LEAN_IMAGE],
                capture_output=True,
                text=True,
                timeout=600,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError) as e:
            # OSError: docker 미설치(FileNotFoundError)/실행 권한, SubprocessError: TimeoutExpired
            logger.error(f"[Lean] 이미지 다운로드 실패: {e}")
            return False
    
    @classmethod
    def check_docker(cls) -> bool:
        """Docker 실행 확인"""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        return False
    
    @classmethod
    def check_image(cls) -> bool:
        """Lean 이미지 존재 확인"""
        try:
            result = subprocess.run(
                ["docker", "images", "-q", LEAN_IMAGE],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return bool(result.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            # OSError: docker 미설치(FileNotFoundError)/실행 권한, SubprocessError: TimeoutExpired
            return False
