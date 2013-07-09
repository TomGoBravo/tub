package hello

import (
    "appengine"
    "appengine/urlfetch"
    "appengine/datastore"
    "appengine/user"
    "html/template"
    "net/http"
    "math"
    "time"
    "strconv"
    "fmt"
    "encoding/csv"
)

type Greeting struct {
    Author  string
    Content string
    Date    time.Time
}

type Measure struct {
    Date         time.Time
    PostedSample int
}

func init() {
    http.HandleFunc("/", root)
    http.HandleFunc("/settub", settub)
    http.HandleFunc("/tub/measure", measure)
}

func measure(w http.ResponseWriter, r *http.Request) {
    c := appengine.NewContext(r)
    val := r.FormValue("value")
    if val != "" {
        valint, err := strconv.Atoi(val)
        if err != nil {
            http.Error(w, err.Error(), http.StatusInternalServerError)
            return
        }
        m := Measure {
            Date:  time.Now(),
            PostedSample: valint,
        }
        _, err = datastore.Put(c, datastore.NewIncompleteKey(c, "Measure", nil), &m)
        if err != nil {
            http.Error(w, err.Error(), http.StatusInternalServerError)
            return
        }
        fmt.Fprint(w, "OK")
    } else {
        limit := 10000
        if r.FormValue("limit") != "" {
            var err error
            limit, err = strconv.Atoi(r.FormValue("limit"))
            if err != nil {
                http.Error(w, err.Error(), http.StatusInternalServerError)
                return
            }
        }
        q := datastore.NewQuery("Measure").Order("Date").Limit(limit)
        measures := make([]Measure, 0, limit)
        if _, err := q.GetAll(c, &measures); err != nil {
            http.Error(w, err.Error(), http.StatusInternalServerError)
            return
        }
        location, err := time.LoadLocation("America/Los_Angeles")
        if err != nil {
            http.Error(w, err.Error(), http.StatusInternalServerError)
            return
        }
        for i, _ := range measures {
            measures[i].Date = measures[i].Date.In(location)
        }
        switch r.FormValue("output") {
        case "csv":
            w.Header().Set("Content-Type", "text/plain; charset=utf-8")
            csvwriter := csv.NewWriter(w)
            csvwriter.Write([]string{"Time", "AtoD"})
            for _, m2 := range measures {
                csvwriter.Write([]string{
                    m2.Date.Format("2006-01-02T15:04:05"),
                    strconv.Itoa(m2.PostedSample)})
            }
            csvwriter.Flush()
        default:
            formatted := make([]struct{Date template.JS; Temp template.JS}, len(measures))
            for i, m := range measures {
                formatted[i].Date = template.JS(
                    "new Date(" + strconv.FormatInt(m.Date.Unix() * 1000, 10) + ")")
                formatted[i].Temp = template.JS(strconv.FormatFloat(
                    sampleToTemp(m.PostedSample), 'f', 1, 64))  // bitSize. WTF Go?
            }
            if err := chartTemplate.Execute(w, formatted); err != nil {
                http.Error(w, err.Error(), http.StatusInternalServerError)
            }
        }
    }
}

// Convert an A2D sample into a temperature in F
func sampleToTemp(sample int) float64 {
    v := float64(sample >> 4) / 4095 * 3.3
    return (v - 5.00690909090911) / (-0.029227272727273)
}

func root(w http.ResponseWriter, r *http.Request) {
    c := appengine.NewContext(r)
    q := datastore.NewQuery("Greeting").Order("-Date").Limit(20)
    greetings := make([]Greeting, 0, 10)
    if _, err := q.GetAll(c, &greetings); err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    q2 := datastore.NewQuery("Measure").Order("-Date").Limit(1)
    measures := make([]Measure, 0, 1)
    if _, err := q2.GetAll(c, &measures); err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    location, err := time.LoadLocation("America/Los_Angeles")
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    for i, _ := range greetings {
        greetings[i].Date = greetings[i].Date.In(location)
    }
    var measuredTemp float64
    var measuredTime time.Time
    if len(measures) > 0 {
        measuredTemp = sampleToTemp(measures[0].PostedSample)
        measuredTime = measures[0].Date.In(location)
    } else {
        measuredTemp = math.NaN()
        measuredTime = time.Now()
    }
    templateparams := struct{Greetings []Greeting; Temp float64; TempDate time.Time}{
        greetings, measuredTemp, measuredTime}
    
    if err := guestbookTemplate.Execute(w, templateparams); err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
    }
}

var guestbookTemplate = template.Must(template.New("book").Parse(guestbookTemplateHTML))

const guestbookTemplateHTML = `
<html>
  <head><title>Tub Control</title></head>
  <body>
    <a href="/settub?value=on">on</a>, <a href="/settub?value=off">off</a>, <a href="/tub/measure">measures</a><p>
    {{with .Greetings}}
    {{range .}}
      At {{.Date.Format "2006-01-02 15:04"}}
      {{with .Author}}
        {{.}}:
      {{else}}
        anonymous:
      {{end}}
      {{.Content}}<br>
    {{end}}
    {{end}}
    {{printf "%0.1f" .Temp}}°F estimated at {{.TempDate.Format "2006-01-02 15:04"}} but this is only correct when on.
  </body>
</html>
`

var chartTemplate = template.Must(template.New("chart").Parse(chartTemplateHTML))
const chartTemplateHTML = `
<html>
  <head>
    <script type='text/javascript' src='http://www.google.com/jsapi'></script>
    <script type='text/javascript'>
      google.load('visualization', '1', {'packages':['annotatedtimeline']});
      google.setOnLoadCallback(drawChart);
      function drawChart() {
        var data = new google.visualization.DataTable();
        data.addColumn('datetime', 'Date');
        data.addColumn('number', 'Temp');
        data.addColumn('string', 'title1');
        data.addColumn('string', 'text1');
        data.addRows([
        {{range .}}
          [{{.Date}}, {{.Temp}}, undefined, undefined], {{end}}
        ]);

        var chart = new google.visualization.AnnotatedTimeLine(document.getElementById('chart_div'));
        chart.draw(data, {displayAnnotations: true, scaleType: "maximized"});
      }
    </script>
  </head>

  <body>
    This is a graph of temperature measured over the thermoresistor <i>and</i> disabling R. In other words, when the Imp has disabled the tub the actual temperature is lower by about 5°F.
    <div id='chart_div' style='width: 700px; height: 240px;'></div>
    <a href="/tub/measure?output=csv">csv</a>
  </body>
</html>
`

func settub(w http.ResponseWriter, r *http.Request) {
    c := appengine.NewContext(r)
    u := user.Current(c)
    if u == nil {
        url, err := user.LoginURL(c, r.URL.String())
        if err != nil {
            http.Error(w, err.Error(), http.StatusInternalServerError)
            return
        }
        w.Header().Set("Location", url)
        w.WriteHeader(http.StatusFound)
        return
    }
    
    var imp_url string
    var content string
    switch r.FormValue("value") {
    case "off":
        imp_url = base_url + "&value=0"
        content = "turned off"
    case "on":
        imp_url = base_url + "&value=1"
        content = "turned on"
    default:
        http.Error(w, "Invalid value", http.StatusBadRequest)
        return
    }
    client := urlfetch.Client(c)
    
    if resp, err := client.Get(imp_url); err != nil {
        content = resp.Status
    }
        
    g := Greeting {
        Content: content,
        Date:    time.Now(),
        Author:  u.String(),
    }
    _, err := datastore.Put(c, datastore.NewIncompleteKey(c, "Greeting", nil), &g)
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    // From http://stackoverflow.com/questions/3413036/http-response-caching
    w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
    w.Header().Set("Pragma", "no-cache")
    w.Header().Set("Expires", "0")
    http.Redirect(w, r, "/", http.StatusFound)
}
