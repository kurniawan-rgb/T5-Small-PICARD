from fastapi.openapi.utils import get_openapi
import shutil
from typing import Optional
from seq2seq.utils.dataset import DataTrainingArguments
from seq2seq.utils.picard_model_wrapper import PicardArguments, PicardLauncher, with_picard
from seq2seq.utils.pipeline import Text2SQLGenerationPipeline, Text2SQLInput
from sqlite3 import Connection, connect, OperationalError
from uvicorn import run
from fastapi import FastAPI, HTTPException, Body, File, UploadFile
from transformers.models.auto import AutoConfig, AutoTokenizer, AutoModelForSeq2SeqLM
from transformers.hf_argparser import HfArgumentParser
from transformers.pipelines.text2text_generation import ReturnType
from contextlib import nullcontext
import os
from pydantic import BaseModel
from dataclasses import dataclass, field
import sys
import logging

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s",
    datefmt="%m/%d/%Y %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
    level=logging.WARNING,
)
logger = logging.getLogger(__name__)


@dataclass
class BackendArguments:
    """
    Arguments pertaining to model serving.
    """

    model_path: str = field(
        default="../transformers_cache/t5-base",
        metadata={"help": "Path to pretrained model"},
    )
    cache_dir: Optional[str] = field(
        default="/tmp",
        metadata={"help": "Where to cache pretrained models and data"},
    )
    db_path: str = field(
        default="/database",
        metadata={"help": "Where to to find the sqlite files"},
    )
    host: str = field(default="0.0.0.0", metadata={
                      "help": "Bind socket to this host"})
    port: int = field(default=8000, metadata={
                      "help": "Bind socket to this port"})
    device: int = field(
        default=-1,
        metadata={
            "help": "Device ordinal for CPU/GPU supports. Setting this to -1 will leverage CPU. A non-negative value will run the model on the corresponding CUDA device id."
        },
    )
    unhas_training_schema: bool = field(
        default=False,
        metadata={
            "help": "Serialize the neosia schema exactly as it was serialized during UNHAS T5-small fine-tuning."
        },
    )
    min_length: int = field(
        default=0,
        metadata={"help": "Minimum decoder length; prevents an empty SQL sequence when using one beam."},
    )


# This order and delimiter deliberately match build_input_text() in the UNHAS
# fine-tuning notebook.  The regular Spider pipeline serializes all 130 tables
# alphabetically, while the model was trained only on this ordered core schema.
UNHAS_CORE_TABLES = (
    "mahasiswa", "prodi", "fakultas", "prodi_jenjang", "jurusan", "peminatan",
    "jalur_masuk", "agama", "semester", "prodi_semester", "kurikulum",
    "mata_kuliah", "mata_kuliah_jumlah_sks", "tipe_sks", "mata_kuliah_prasyarat",
    "dosen", "kelas_kuliah", "kelas_kuliah_jenis", "kelas_kuliah_has_dosen",
    "kelas_kuliah_has_mahasiswa", "kelas_kuliah_nilai", "kelas_kuliah_nilai_akhir",
    "bobot_nilai_kelas_kuliah", "tipe_bobot_nilai", "kartu_rencana_studi",
    "transkrip", "indeks_prestasi", "riwayat_status_mahasiswa", "status_mahasiswa",
    "penasihat_akademik", "gedung", "ruang", "guna_ruang", "jadwal_kuliah",
    "jadwal_kuliah_has_dosen", "pertemuan", "status_pertemuan", "presensi_mahasiswa",
    "presensi_dosen", "tipe_kehadiran", "tugas_akhir", "status_tugas_akhir",
    "pembimbing_tugas_akhir", "role_pembimbing", "pendadaran_tugas_akhir",
    "penguji_pendadaran_tugas_akhir", "role_penguji_pendadaran",
    "status_lulus_pendadaran", "referensi_konversi_nilai", "pejabat",
    "mahasiswa_pembayaran",
)


class UnhasText2SQLGenerationPipeline(Text2SQLGenerationPipeline):
    """Serving input formatter compatible with the UNHAS T5-small checkpoint."""

    def __init__(
        self,
        *args,
        picard_db_prefix_adapter: bool = False,
        preserve_db_id: bool = False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.picard_db_prefix_adapter = picard_db_prefix_adapter
        self.preserve_db_id = preserve_db_id

    def _pre_process(self, input: Text2SQLInput) -> str:
        # Preserve the standard behavior for any database other than neosia.
        if input.db_id != "neosia":
            return super()._pre_process(input)

        if input.db_id not in self.schema_cache:
            from seq2seq.utils.pipeline import get_schema
            self.schema_cache[input.db_id] = get_schema(db_path=self.db_path, db_id=input.db_id)
        schema = self.schema_cache[input.db_id]

        # PICARD still receives the complete database metadata for SQL parsing.
        if self.picard_db_prefix_adapter and hasattr(self.model, "set_picard_db_id"):
            self.model.set_picard_db_id(input.db_id)
        if hasattr(self.model, "add_schema"):
            self.model.add_schema(db_id=input.db_id, db_info=schema)

        columns_by_table = {table: [] for table in schema["db_table_names"]}
        for table_id, column_name in zip(
            schema["db_column_names"]["table_id"],
            schema["db_column_names"]["column_name"],
        ):
            columns_by_table[schema["db_table_names"][table_id]].append(column_name)

        tables = [
            f"{table} : {', '.join(columns_by_table[table])}"
            for table in UNHAS_CORE_TABLES
            if table in columns_by_table
        ]
        if not tables:
            return super()._pre_process(input)

        prefix = self.prefix if self.prefix is not None else ""
        return f"{prefix}{input.utterance.strip()} | {input.db_id} | {' | '.join(tables)}"

    def postprocess(
        self,
        model_outputs: dict,
        return_type=ReturnType.TEXT,
        clean_up_tokenization_spaces=False,
    ):
        """Keep ``db_id | SQL`` for PICARD-native checkpoints.

        The base Spider pipeline removes everything before the first ``|``.
        That behavior is correct for standard Spider outputs but destroys the
        database prefix required by a model trained with PICARD targets.
        """
        if not self.preserve_db_id:
            return super().postprocess(
                model_outputs,
                return_type=return_type,
                clean_up_tokenization_spaces=clean_up_tokenization_spaces,
            )

        records = []
        for output_ids in model_outputs["output_ids"][0]:
            if return_type == ReturnType.TENSORS:
                record = {f"{self.return_name}_token_ids": model_outputs}
            else:
                record = {
                    f"{self.return_name}_text": self.tokenizer.decode(
                        output_ids,
                        skip_special_tokens=True,
                        clean_up_tokenization_spaces=clean_up_tokenization_spaces,
                    ).strip()
                }
            records.append(record)
        return records


def delete_folders(path, dir_to_keep):
    # get a list of all the subdirectories in the specified path
    subdirectories = [d for d in os.listdir(
        path) if os.path.isdir(os.path.join(path, d))]
    for subdir in subdirectories:
        if subdir == dir_to_keep:
            continue
        subdir_path = os.path.join(path, subdir)
        shutil.rmtree(subdir_path)


def main():
    # See all possible arguments by passing the --help flag to this program.
    parser = HfArgumentParser(
        (PicardArguments, BackendArguments, DataTrainingArguments))
    picard_args: PicardArguments
    backend_args: BackendArguments
    data_training_args: DataTrainingArguments
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        # If we pass only one argument to the script and it's the path to a json file,
        # let's parse it to get our arguments.
        picard_args, backend_args, data_training_args = parser.parse_json_file(
            json_file=os.path.abspath(sys.argv[1]))
    else:
        picard_args, backend_args, data_training_args = parser.parse_args_into_dataclasses()

    # Initialize config
    config = AutoConfig.from_pretrained(
        backend_args.model_path,
        cache_dir=backend_args.cache_dir,
        max_length=data_training_args.max_target_length,
        min_length=backend_args.min_length,
        num_beams=data_training_args.num_beams,
        num_beam_groups=data_training_args.num_beam_groups,
        diversity_penalty=data_training_args.diversity_penalty,
    )

    # Initialize tokenizer
    tokenizer = AutoTokenizer.from_pretrained(
        backend_args.model_path,
        cache_dir=backend_args.cache_dir,
        use_fast=True,
    )

    # Initialize Picard if necessary
    with PicardLauncher() if picard_args.launch_picard else nullcontext(None):
        # Get Picard model class wrapper
        if picard_args.use_picard:
            def model_cls_wrapper(model_cls): return with_picard(
                model_cls=model_cls, picard_args=picard_args, tokenizer=tokenizer
            )
        else:
            def model_cls_wrapper(model_cls): return model_cls

        # Initialize model
        model = model_cls_wrapper(AutoModelForSeq2SeqLM).from_pretrained(
            backend_args.model_path,
            config=config,
            cache_dir=backend_args.cache_dir,
        )

        # Initalize generation pipeline
        pipeline_cls = UnhasText2SQLGenerationPipeline if backend_args.unhas_training_schema else Text2SQLGenerationPipeline
        pipeline_kwargs = dict(
            model=model,
            tokenizer=tokenizer,
            db_path=backend_args.db_path,
            prefix=data_training_args.source_prefix,
            normalize_query=data_training_args.normalize_query,
            schema_serialization_type=data_training_args.schema_serialization_type,
            schema_serialization_with_db_id=data_training_args.schema_serialization_with_db_id,
            schema_serialization_with_db_content=data_training_args.schema_serialization_with_db_content,
            device=backend_args.device,
        )
        if pipeline_cls is UnhasText2SQLGenerationPipeline:
            # Old checkpoints generate SQL directly; new PICARD checkpoints
            # generate ``db_id | SQL`` and must not receive a duplicate prefix.
            pipeline_kwargs["picard_db_prefix_adapter"] = picard_args.use_picard and not data_training_args.target_with_db_id
            pipeline_kwargs["preserve_db_id"] = data_training_args.target_with_db_id
        pipe = pipeline_cls(**pipeline_kwargs)

        # Initialize REST API
        app = FastAPI()

        def custom_openapi():
            if app.openapi_schema:
                return app.openapi_schema
            openapi_schema = get_openapi(
                title="EZ-PICARD",
                version="0.0.1",
                description="EZ-PICARD is a eazy implementation of the PICARD framework for text-to-SQL generation.",
                routes=app.routes,
            )
            app.openapi_schema = openapi_schema
            return app.openapi_schema

        app.openapi = custom_openapi

        class AskResponse(BaseModel):
            query: str
            execution_results: list
            decoder_trace: Optional[dict] = None

        def response(
            query: str,
            conn: Connection,
            db_id: str,
            decoder_trace: Optional[dict] = None,
        ) -> AskResponse:
            sql = query.strip()
            if data_training_args.target_with_db_id:
                expected_prefix = f"{db_id} |"
                if not sql.startswith(expected_prefix):
                    raise HTTPException(status_code=422, detail=f'PICARD target must start with "{expected_prefix}"')
                sql = sql[len(expected_prefix):].strip()
            if not sql:
                raise HTTPException(status_code=422, detail="PICARD generated an empty SQL query")
            try:
                return AskResponse(
                    query=sql,
                    execution_results=conn.execute(sql).fetchall(),
                    decoder_trace=decoder_trace,
                )
            except OperationalError as e:
                message = f'while executing "{sql}", the following error occurred: {e.args[0]}'
                if decoder_trace is not None:
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "message": message,
                            "query": sql,
                            "decoder_trace": decoder_trace,
                        },
                    )
                raise HTTPException(status_code=500, detail=message)

        @app.get("/ask/{db_id}/{question}")
        def ask(db_id: str = 'chinook', question: str = 'how many singers we have?'):
            try:
                outputs = pipe(
                    inputs=Text2SQLInput(utterance=question, db_id=db_id),
                    num_return_sequences=data_training_args.num_return_sequences,
                )
            except OperationalError as e:
                raise HTTPException(status_code=404, detail=e.args[0])
            try:
                conn = connect(
                    f"{backend_args.db_path}/{db_id}/{db_id}.sqlite")
                decoder_trace = None
                if picard_args.use_picard and hasattr(pipe.model, "get_picard_trace"):
                    decoder_trace = pipe.model.get_picard_trace()
                return [
                    response(
                        query=output["generated_text"],
                        conn=conn,
                        db_id=db_id,
                        decoder_trace=decoder_trace,
                    )
                    for output in outputs
                ]
            finally:
                conn.close()

        @app.get("/dbs")
        def dbs():
            return [db for db in os.listdir(backend_args.db_path) if os.path.isdir(os.path.join(backend_args.db_path, db))]

        @app.post("/upload/")
        async def upload(file: UploadFile = File(...)):
            try:
                # delete_folders(backend_args.db_path, "chinook")
                path = f'{backend_args.db_path}/{file.filename.split(".")[0]}'
                os.makedirs(path, exist_ok=True)
                with open(f'{path}/{file.filename}', 'wb') as f:
                    shutil.copyfileobj(file.file, f)
            except Exception as e:
                raise HTTPException(status_code=400, detail=e)
            finally:
                file.file.close()
            return {"message": f"Successfully uploaded {file.filename}"}

        # # post request that gets configs, db_id and question and model_path
        @app.post("/ask/{db_id}/{question}")
        def ask(db_id: str = 'chinook', question: str = 'how many singers we have?', backend_args: dict = Body(...)):
            try:
                config = AutoConfig.from_pretrained(
                    backend_args['model_path'],
                    cache_dir=backend_args['cache_dir'],
                    max_length=data_training_args.max_target_length,
                    min_length=backend_args.min_length,
                    num_beams=data_training_args.num_beams,
                    num_beam_groups=data_training_args.num_beam_groups,
                    diversity_penalty=data_training_args.diversity_penalty,
                )
                model = model_cls_wrapper(AutoModelForSeq2SeqLM).from_pretrained(
                    backend_args["model_path"],
                    config=config,
                    cache_dir=backend_args['cache_dir'],
                )
                pipeline_kwargs = dict(
                    model=model,
                    tokenizer=tokenizer,
                    db_path=backend_args['db_path'],
                    prefix=data_training_args.source_prefix,
                    normalize_query=data_training_args.normalize_query,
                    schema_serialization_type=data_training_args.schema_serialization_type,
                    schema_serialization_with_db_id=data_training_args.schema_serialization_with_db_id,
                    schema_serialization_with_db_content=data_training_args.schema_serialization_with_db_content,
                    device=backend_args['device'],
                )
                if pipeline_cls is UnhasText2SQLGenerationPipeline:
                    pipeline_kwargs["picard_db_prefix_adapter"] = picard_args.use_picard and not data_training_args.target_with_db_id
                    pipeline_kwargs["preserve_db_id"] = data_training_args.target_with_db_id
                pipe = pipeline_cls(**pipeline_kwargs)
                outputs = pipe(
                    inputs=Text2SQLInput(utterance=question, db_id=db_id),
                    num_return_sequences=1,
                )
            except OperationalError as e:
                raise HTTPException(status_code=404, detail=e)
            try:
                conn = connect(
                    f"{backend_args.db_path}/{db_id}/{db_id}.sqlite")
                return [response(query=output["generated_text"], conn=conn, db_id=db_id) for output in outputs]
            finally:
                conn.close()

        # Run app
        run(app=app, host=backend_args.host, port=backend_args.port)


if __name__ == "__main__":
    main()
