module.exports = {
  apps: [{
    name: "Thu_vien_PL_check_hieu_luc",
    script: ".venv/bin/uvicorn",
    args: "check_hieu_luc.api:app --host 0.0.0.0 --port 9000",
    cwd: __dirname,
    interpreter: "none",
    env: {
      PATH: __dirname + "/.venv/bin:" + process.env.PATH
    },
    autorestart: true,
    watch: false,
    max_restarts: 5,
    max_memory_restart: "2G"
  }]
}
