package com.allprice.app

import android.app.*
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

class ServerService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(NotificationChannel(
                    "allprice", "全价比价", NotificationManager.IMPORTANCE_LOW))
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val apiKey = getSharedPreferences("allprice", MODE_PRIVATE).getString("api_key", "") ?: ""
        val logDir = filesDir.absolutePath
        val statusFile = File(logDir, "allprice_status.txt")
        statusFile.delete()

        startForeground(1, NotificationCompat.Builder(this, "allprice")
            .setContentTitle("全价比价").setContentText("服务运行中")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setOngoing(true).setPriority(NotificationCompat.PRIORITY_LOW).build())

        Thread {
            try {
                if (!Python.isStarted()) Python.start(AndroidPlatform(this))
                val result = Python.getInstance().getModule("server_runner")
                    .callAttr("start_server", apiKey, "127.0.0.1", 8001, logDir)

                val status = result.get("status")?.toString() ?: "unknown"
                if (status == "error") {
                    // Error already written by Python
                }
            } catch (e: Exception) {
                try {
                    statusFile.writeText("error_${e.message ?: "unknown"}")
                } catch (_: Exception) {}
            }
        }.start()

        return START_STICKY
    }
}
