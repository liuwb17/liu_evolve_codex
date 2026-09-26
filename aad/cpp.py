"""Compile once, then evaluate C++ candidates in restricted Docker containers."""
import concurrent.futures
import json
import subprocess
import uuid
from pathlib import Path
from .io import digest, read_json, save_json
from .problem import judge
from .runner import execute, summarize


class CppEvaluator:
    def __init__(self, root: Path, workers=4, seconds=0.5, container_timer=False):
        self.root=root.resolve(); self.root.mkdir(parents=True,exist_ok=True)
        self.workers=workers; self.seconds=seconds
        self.container_timer=container_timer
        result=subprocess.run(["docker","image","inspect","mosaic-cpp:1","--format","{{.Id}}"],capture_output=True,text=True,check=True)
        self.image=result.stdout.strip()

    def compile(self, code):
        folder=self.root/"builds"/digest(code)
        folder.mkdir(parents=True,exist_ok=True)
        if (folder/"compiled.json").exists():
            record=read_json(folder/"compiled.json")
            if record["image"]==self.image and (folder/"solution").exists():return folder,None
        (folder/"source.cpp").write_text(code,encoding="utf-8")
        name="aad-build-"+uuid.uuid4().hex
        command=["docker","run","--rm","--name",name,"--network=none","--memory=1g","--cpus=2",
                 "--pids-limit=64","--cap-drop=ALL","--security-opt=no-new-privileges","--read-only",
                 "--tmpfs","/tmp:rw,size=256m","--mount",f"type=bind,source={folder},target=/work",
                 self.image,"g++","-std=c++17","-O3","-DNDEBUG","/work/source.cpp","-o","/work/solution"]
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=60)
        except subprocess.TimeoutExpired:
            return folder,"Compiler exceeded 60 seconds"
        finally:
            subprocess.run(["docker","rm","-f",name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        if result.returncode:return folder,result.stderr[-8000:]
        save_json(folder/"compiled.json",{"image":self.image,"source_sha256":digest(code),"flags":"-std=c++17 -O3 -DNDEBUG"})
        return folder,None

    def evaluate(self,code,cases):
        folder,error=self.compile(code)
        if error:return summarize([{"name":c.name,"status":"CE","score":0,"seconds":0,"message":error} for c in cases])
        def one(case):
            timing_version='cpp-v2-runtime5' if self.container_timer else 'cpp-v1'
            key=digest(code+case.fingerprint+self.image+str(self.seconds)+timing_version)
            cache=self.root/"cache"/(key+".json")
            if cache.exists():return read_json(cache)
            name="aad-cpp-"+uuid.uuid4().hex
            command=["docker","run","--rm","--name",name,"--network=none","--read-only",
                     "--cap-drop=ALL","--security-opt=no-new-privileges","--pids-limit=32",
                     "--memory=512m","--cpus=1","--user=65534:65534","--mount",
                     f"type=bind,source={folder},target=/work,readonly","-i",self.image,
                     "/work/solution",str(self.seconds)]
            if self.container_timer:
                project=Path(__file__).resolve().parents[1]
                command=command[:-3]+[
                    '--mount',f'type=bind,source={project / "scripts"},target=/harness,readonly',
                    '--mount',f'type=bind,source={project / "aad"},target=/library/aad,readonly',
                    self.image,'python3','-B','/harness/cpp_case_driver.py',str(self.seconds)]
            try:
                result=execute(command,case.text,folder,30.0 if self.container_timer else 5.0)
                if self.container_timer:
                    outer=result
                    if outer['status']=='AC':
                        try:result=json.loads(outer['stdout'])
                        except (ValueError,TypeError):
                            result={**outer,'status':'RE','stderr':'Invalid timer result','stdout':''}
                    else:result={**outer,'status':'INFRA_ERROR'}
                    result['container_wall_seconds']=outer['seconds']
            finally:subprocess.run(["docker","rm","-f",name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
            if result["status"]=="AC":result.update(judge(case.text,result["stdout"]))
            else:result.update(score=0,message=result["stderr"][-2000:])
            result.update(name=case.name,input_sha256=case.fingerprint)
            save_json(cache,result)
            return result
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:
            return summarize(list(pool.map(one,cases)))
