LANGKAH AWAL :
ssh hydroships@192.168.2.2
password : (spasi 1 kali)

cara cek 
sudo systemctl start rov-agent (start service)
sudo systemctl restart rov-agent (reload service kalau ada perubahan)
sudo systemctl stop rov-agent (stop rov-agent)
sudo systemctl status rov-agent (cek status rov-agent)
journalctl -u rov-agent -f (cara melihat log)
nano ~/rov-agent/rov_agent.py (edit file.py)

cara shutdown 
sudo power off 

sudo systemctl restart rov-agent
journalctl -u rov-agent -f